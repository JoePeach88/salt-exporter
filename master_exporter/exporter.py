import time
import prometheus_client as prom
import logging
import sys
import importlib
import argparse
from pathlib import Path
from env import *


def import_parents(level=1):
    global __package__
    file = Path(__file__).resolve()
    parent, top = file.parent, file.parents[level]
    sys.path.append(str(top))
    try:
        sys.path.remove(str(parent))
    except ValueError:
        pass
    __package__ = '.'.join(parent.parts[len(top.parts):])
    importlib.import_module(__package__)


import_parents(2)
from ..modules.salt_master_local_client import salt_runner, salt_key
__virtualname__ = "salt_master_metrics"
log = logging.getLogger(__name__)
formatter = logging.Formatter(fmt="%(message)s")
handler = logging.StreamHandler(stream=sys.stdout)
handler.setFormatter(formatter)
log.addHandler(handler)


class SaltMetricsExporter:
    METRICS_INFO = {
        'total_jobs': ('salt_all_jobs_total', 'Total jobs count.'),
        'total_active_jobs': ('salt_active_jobs_total', 'Total active jobs count.'),
        'minions_up_count': ('salt_minions_up_total', 'Total up minions count.'),
        'minions_down_count': ('salt_minions_down_total', 'Total down minions count.'),
        'total_minions_count': ('salt_minions_total', 'Total minions count.'),
        'accepted_minions_count': ('salt_accepted_minions_total', 'Total accepted minions count.'),
        'denied_minions_count': ('salt_denied_minions_total', 'Total denied minions count.'),
        'rejected_minions_count': ('salt_rejected_minions_total', 'Total rejected minions count.'),
        'unaccepted_minions_count': ('salt_unaccepted_minions_total', 'Total unaccepted minions count.'),
    }

    def __init__(self):
        self.metrics = self._create_metrics()

    def _create_metrics(self):
        return {
            key: prom.Gauge(name, desc)
            for key, (name, desc) in self.METRICS_INFO.items()
        }

    def collect_data_counts(self):
        try:
            log.info('Starting collecting data...')
            minion_statuses = salt_runner.cmd('manage.status', print_event=False)
            job_list = salt_runner.cmd('jobs.list_jobs', print_event=False)
            active_jobs_list = salt_runner.cmd('jobs.active', print_event=False)
            key_data = salt_key.list_keys()
            minions_up = len(minion_statuses.get('up', []))
            minions_down = len(minion_statuses.get('down', []))
            log.info('Data collected.')
        except Exception as e:
            minion_statuses = []
            job_list = []
            active_jobs_list = []
            key_data = {}
            minions_up = 0
            minions_down = 0
            log.error(f'Something went wrong when trying to collect metrics data: {repr(e)}')
        return {
            'total_jobs': len(job_list),
            'total_active_jobs': len(active_jobs_list),
            'minions_up_count': minions_up,
            'minions_down_count': minions_down,
            'total_minions_count': minions_up + minions_down,
            'accepted_minions_count': len(key_data.get('minions', [])),
            'denied_minions_count': len(key_data.get('minions_denied', [])),
            'rejected_minions_count': len(key_data.get('minions_rejected', [])),
            'unaccepted_minions_count': len(key_data.get('minions_pre', [])),
        }

    def update_metrics(self, counts):
        log.info('Updating metrics...')
        try:
            for key, value in counts.items():
                metric = self.metrics.get(key)
                if metric is not None:
                    metric.set(value)
            log.info('Metrics updated.')
        except Exception as e:
            log.error(f'Something went wrong when trying to update metrics data: {repr(e)}')

    def run(self, addr=None, port=None, delay=None):
        server = prom.start_http_server(port if port else EXPORTER_PORT, addr=addr if addr else EXPORTER_ADDR)
        log.info(f"Server started on {':'.join(str(x) for x in server[0].server_address)}")
        while True:
            counts = self.collect_data_counts()
            self.update_metrics(counts)
            time.sleep(delay if delay else EXPORTER_COLLECT_DELAY)


if __name__ == '__main__':
    try:
        parser = argparse.ArgumentParser()
        parser.add_argument(
            '--addr',
            help='The address where the server will operate.'
        )
        parser.add_argument(
            '--port',
            type=int,
            help='The port where the server will operate.'
        )
        parser.add_argument(
            '--delay',
            type=int,
            help='The delay between metrics updates.'
        )
        args = parser.parse_args()
        exporter = SaltMetricsExporter()
        exporter.run(args.addr, args.port, args.delay)
    except KeyboardInterrupt:
        log.info('Server stopped by user.')
