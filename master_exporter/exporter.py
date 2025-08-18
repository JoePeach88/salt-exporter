import time
import prometheus_client as prom
import logging
import sys
import importlib
import argparse
import traceback
import gc
from pathlib import Path
from env import *
from datetime import datetime


def import_parents(level: int = 1):
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
gc.set_debug(gc.DEBUG_SAVEALL)


class SaltMetricsExporter:
    METRICS_INFO = {
        'total_jobs': {
            'name': 'salt_all_jobs_total',
            'desc': 'Total jobs count for current date.',
            'type': prom.Gauge
        },
        'total_active_jobs': {
            'name': 'salt_active_jobs_total',
            'desc': 'Total active jobs count.',
            'type': prom.Gauge
        },
        'minions_up_count': {
            'name': 'salt_minions_up_total',
            'desc': 'Total up minions count.',
            'type': prom.Gauge
        },
        'minions_down_count': {
            'name': 'salt_minions_down_total',
            'desc': 'Total down minions count.',
            'type': prom.Gauge
        },
        'total_minions_count': {
            'name': 'salt_minions_total',
            'desc': 'Total minions count.',
            'type': prom.Gauge
        },
        'accepted_minions_count': {
            'name': 'salt_accepted_minions_total',
            'desc': 'Total accepted minions count.',
            'type': prom.Gauge
        },
        'denied_minions_count': {
            'name': 'salt_denied_minions_total',
            'desc': 'Total denied minions count.',
            'type': prom.Gauge
        },
        'rejected_minions_count': {
            'name': 'salt_rejected_minions_total',
            'desc': 'Total rejected minions count.',
            'type': prom.Gauge
        },
        'unaccepted_minions_count': {
            'name': 'salt_unaccepted_minions_total',
            'desc': 'Total unaccepted minions count.',
            'type': prom.Gauge
        },
        'minion_job_duration': {
            'name': 'salt_minion_job_duration_seconds',
            'desc': 'Duration of Salt jobs in seconds.',
            'type': prom.Summary,
            'labels': ['minion', 'jid', 'fun']
        },
        'minion_job_retcode': {
            'name': 'salt_minion_job_retcode',
            'desc': 'Retcode of Salt job.',
            'type': prom.Gauge,
            'labels': ['minion', 'fun']
        },
        'minion_status': {
            'name': 'salt_minion_status',
            'desc': 'Status of salt-minion (0 - offline, 1 - online).',
            'type': prom.Gauge,
            'labels': ['minion']
        }
    }

    def __init__(self):
        self.metrics = self._create_metrics()

    def _create_metrics(self):
        metrics = {}
        try:
            for key, meta in self.METRICS_INFO.items():
                metric_type = meta.get('type', prom.Gauge)
                name = meta.get('name')
                desc = meta.get('desc')
                labels = meta.get('labels', None)
                metrics.update({
                    key: {
                        'metric': metric_type(name, desc, labelnames=labels if labels else ()),
                        'type': 'labeled' if labels else 'not_labeled'
                    }
                })
            return metrics
        except Exception:
            log.error(f'Something went wrong when trying to create metrics: {traceback.format_exc()}')

    def collect_data(self):
        metrics = {
            'minion_status': [],
            'minion_job_duration': [],
            'minion_job_retcode': []
        }
        try:
            log.info('Starting collecting data...')
            minion_statuses = salt_runner.cmd('manage.status', print_event=EXPORTER_DEBUG)
            job_list = salt_runner.cmd('jobs.list_jobs', kwarg={
                'start_time': datetime.today().replace(hour=0, minute=0, second=0).strftime("%Y, %b %d %H:%M:%S"),
                'end_time': datetime.today().replace(hour=23, minute=59, second=59).strftime("%Y, %b %d %H:%M:%S")
            }, print_event=EXPORTER_DEBUG)
            active_jobs_list = salt_runner.cmd('jobs.active', print_event=EXPORTER_DEBUG)
            key_data = salt_key.list_keys()
            minions_up = minion_statuses.get('up', [])
            minions_down = minion_statuses.get('down', [])
            log.info('Collecting down minions...')
            for minion in minions_down:
                metrics['minion_status'].append({
                    'minion': minion,
                    'value': 0
                })
            log.info('Collected.')
            log.info('Collecting up minions...')
            for minion in minions_up:
                metrics['minion_status'].append({
                    'minion': minion,
                    'value': 1
                })
            log.info('Collected.')
            log.info('Collecting jobs...')
            all_minions = minions_up + minions_down
            for minion in all_minions:
                last_jobs = salt_runner.cmd('jobs.last_run', kwarg={'target': minion}, print_event=EXPORTER_DEBUG)
                if not last_jobs:
                    continue

                for jid, jobs_data in last_jobs.items():
                    fun = jobs_data.get('Function')
                    job_result = jobs_data.get('Result', {}).get(minion, {})

                    if not isinstance(job_result, dict) or not job_result:
                        continue

                    if fun in EXPORTER_EXCLUDED_FUNCTIONS:
                        continue

                    job_duration = 0
                    job_return = job_result.get('return')
                    if isinstance(job_return, dict):
                        for job_data in job_return.values():
                            if isinstance(job_data, dict):
                                job_duration += job_data.get('duration')

                    metrics['minion_job_duration'].append({
                        'minion': minion,
                        'jid': jid,
                        'fun': fun,
                        'value': job_duration
                    })
                    metrics['minion_job_retcode'].append({
                        'minion': minion,
                        'fun': fun,
                        'value': job_result.get('retcode')
                    })
            log.info('Collected.')
            log.info('All data collected successfully!')
        except Exception:
            log.error(f'Something went wrong when trying to collect metrics data: {traceback.format_exc()}\n{job_return}')
            minions_up = []
            minions_down = []
            job_list = {}
            active_jobs_list = []
            key_data = {}

        metrics.update({
            'total_jobs': {
                'value': len(job_list)
            },
            'total_active_jobs': {
                'value': len(active_jobs_list)
            },
            'minions_up_count': {
                'value': len(minions_up)
            },
            'minions_down_count': {
                'value': len(minions_down)
            },
            'total_minions_count': {
                'value': len(minions_up) + len(minions_down)
            },
            'accepted_minions_count': {
                'value': len(key_data.get('minions', []))
            },
            'denied_minions_count': {
                'value': len(key_data.get('minions_denied', []))
            },
            'rejected_minions_count': {
                'value': len(key_data.get('minions_rejected', []))
            },
            'unaccepted_minions_count': {
                'value': len(key_data.get('minions_pre', []))
            }
        })

        if EXPORTER_DEBUG:
            print(f'Collected metrics: {metrics}')

        return metrics

    def update_metrics(self, counts: dict):
        log.info('Updating metrics...')

        def _set_or_observe(metric_obj, value):
            if isinstance(metric_obj, prom.Gauge):
                metric_obj.set(value)
            else:
                metric_obj.observe(value)

        try:
            for key, value in counts.items():
                metric = self.metrics.get(key)
                if metric is None:
                    continue
                metric_obj = metric['metric']
                metric_type = metric.get('type', 'not_labeled')
                if metric_type == 'not_labeled':
                    _set_or_observe(metric_obj, value['value'])
                else:
                    for metric_data in value:
                        labels = {k: v for k, v in metric_data.items() if k != 'value'}
                        val = metric_data['value']
                        labeled_metric = metric_obj.labels(**labels)
                        _set_or_observe(labeled_metric, val)
            log.info('Metrics updated.')
        except Exception:
            log.error(f'Something went wrong when trying to update metrics data: {traceback.format_exc()}\n{value}')

    def run(self, addr: str = None, port: int = None, delay: int = None):
        server = prom.start_http_server(port if port else EXPORTER_PORT, addr=addr if addr else EXPORTER_ADDR)
        log.info(f"Server started on {':'.join(str(x) for x in server[0].server_address)}")
        gc.collect()
        while True:
            counts = self.collect_data()
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
