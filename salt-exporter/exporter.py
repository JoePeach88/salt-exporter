import socket
import prometheus_client as prom
import logging
import sys
import argparse
import traceback
import gc
import json
import asyncio
import requests
import subprocess
from env import *
from datetime import datetime
from flask import Flask, request, jsonify
from threading import Thread
from concurrent.futures import ThreadPoolExecutor, as_completed
from modules.salt_master_local_client import salt_runner, salt_key, salt_print_job, salt_list_jobs
if EXPORTER_DEBUG:
    import tracemalloc
    import linecache


# https://stackoverflow.com/a/45679009
def display_top(snapshot, key_type='lineno', limit=10):
    snapshot = snapshot.filter_traces((
        tracemalloc.Filter(False, "<frozen importlib._bootstrap>"),
        tracemalloc.Filter(False, "<unknown>"),
    ))
    top_stats = snapshot.statistics(key_type)

    log.info("Top %s lines" % limit)
    for index, stat in enumerate(top_stats[:limit], 1):
        frame = stat.traceback[0]
        filename = os.sep.join(frame.filename.split(os.sep)[-2:])
        log.info("#%s: %s:%s: %.1f KiB count: %s" % (index, filename, frame.lineno, stat.size / 1024, stat.count))
        line = linecache.getline(frame.filename, frame.lineno).strip()
        if line:
            log.info('    %s' % line)

    other = top_stats[limit:]
    if other:
        size = sum(stat.size for stat in other)
        log.info("%s other: %.1f KiB" % (len(other), size / 1024))
    total = sum(stat.size for stat in top_stats)
    log.info("Total allocated size: %.1f KiB" % (total / 1024))


__virtualname__ = "salt_exporter"
__version__ = '1.02'
log = logging.getLogger(__name__)
formatter = logging.Formatter(fmt="%(message)s")
handler = logging.StreamHandler(stream=sys.stdout)
handler.setFormatter(formatter)
log.addHandler(handler)
gc.enable()
gc.set_debug(0)
gc.set_threshold(700, 10, 10)


def check_reachable(host: str):
    return subprocess.call(f"ping -c 1 {host}".split(' '), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) == 0


def check_port(host: str, port: int):
    return subprocess.call(f"nc -zv {host} {port}".split(' '), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) == 0


class SaltMetricsExporter:
    METRICS_INFO = {
        'salt_all_jobs_total': {
            'name': 'salt_all_jobs_total',
            'desc': 'Total jobs count for current date.',
            'type': prom.Gauge
        },
        'salt_active_jobs_total': {
            'desc': 'Total active jobs count.',
            'type': prom.Gauge
        },
        'salt_minions_up_total': {
            'desc': 'Total up minions count.',
            'type': prom.Gauge
        },
        'salt_minions_down_total': {
            'desc': 'Total down minions count.',
            'type': prom.Gauge
        },
        'salt_minions_total': {
            'desc': 'Total minions count.',
            'type': prom.Gauge
        },
        'salt_accepted_minions_total': {
            'desc': 'Total accepted minions count.',
            'type': prom.Gauge
        },
        'salt_denied_minions_total': {
            'desc': 'Total denied minions count.',
            'type': prom.Gauge
        },
        'salt_rejected_minions_total': {
            'desc': 'Total rejected minions count.',
            'type': prom.Gauge
        },
        'salt_unaccepted_minions_total': {
            'desc': 'Total unaccepted minions count.',
            'type': prom.Gauge
        },
        'salt_minion_job_duration_seconds': {
            'desc': 'Duration of Salt jobs in seconds.',
            'type': prom.Gauge,
            'labels': ['master', 'minion', 'jid', 'fun']
        },
        'salt_minion_job_retcode': {
            'desc': 'Retcode of Salt job.',
            'type': prom.Gauge,
            'labels': ['master', 'minion', 'fun']
        },
        'salt_minion_status': {
            'desc': 'Status of salt-minion (0 - offline, 1 - online).',
            'type': prom.Gauge,
            'labels': ['minion']
        }
    }

    def __init__(self):
        self.metrics = self._create_metrics()
        self.current_metrics = {}
        self.received_metrics = {}

    def _create_metrics(self):
        log.info("Creating metrics...")
        metrics = {}
        try:
            for name, meta in self.METRICS_INFO.items():
                metric_type = meta.get('type', prom.Gauge)
                desc = meta.get('desc')
                mode = meta.get('mode', 'single')
                labels = meta.get('labels', None)
                metrics.update({
                    name: {
                        'metric': metric_type(name, desc, labelnames=labels if labels else ()),
                        'type': 'labeled' if labels else 'not_labeled',
                        'mode': mode
                    }
                })
            log.info('Metrics created.')
            return metrics
        except Exception:
            log.error(f'Something went wrong when trying to create metrics: {traceback.format_exc()}')

    def collect_data(self):
        metrics = {
            'salt_minion_status': [],
            'salt_minion_job_duration_seconds': [],
            'salt_minion_job_retcode': []
        }
        try:
            log.info('Starting collecting data...')
            log.info('Collecting minion statuses...')
            minion_statuses = salt_runner.cmd('manage.status', print_event=EXPORTER_DEBUG)
            log.info('Collected.')

            log.info('Collecting jobs...')
            start_time = datetime.today().replace(hour=0, minute=0, second=0).strftime("%Y, %b %d %H:%M:%S")
            end_time = datetime.today().replace(hour=23, minute=59, second=59).strftime("%Y, %b %d %H:%M:%S")
            job_list = salt_list_jobs(start_time=start_time, end_time=end_time)
            job_list = dict(sorted(job_list.items(), reverse=True))
            log.info('Collected.')

            log.info('Collecting active jobs...')
            active_jobs_list = salt_runner.cmd('jobs.active', print_event=EXPORTER_DEBUG)
            log.info('Collected.')

            key_data = salt_key.list_keys()

            minions_up = minion_statuses.get('up', [])
            minions_down = minion_statuses.get('down', [])

            log.info('Preparing down minions metric...')
            down_metrics = [{'minion': m, 'value': 0} for m in minions_down]
            metrics['salt_minion_status'].extend(down_metrics)
            log.info('Prepared.')

            log.info('Preparing up minions metric...')
            up_metrics = [{'minion': m, 'value': 1} for m in minions_up]
            metrics['salt_minion_status'].extend(up_metrics)
            log.info('Prepared.')

            all_minions = minions_up + minions_down

            def process_minion(minion):
                matching_job_ids = (
                    int(job_id) for job_id, details in job_list.items()
                    if details.get('Target') == minion
                    and not any(p.match(details.get('Function', '')) for p in EXCLUDED_PATTERNS)
                    and any(p.match(details.get('Function', '')) for p in INCLUDED_PATTERNS)
                )
                try:
                    last_job = max(matching_job_ids)
                except ValueError:
                    return None

                last_job_details = salt_print_job(last_job)
                if not last_job_details:
                    return None

                first_key = next(iter(last_job_details), None)
                if first_key is None:
                    return None

                job_data = last_job_details[first_key]
                fun = job_data.get('Function', '')
                job_result = job_data.get('Result', {}).get(minion)

                if not isinstance(job_result, dict) or not job_result:
                    return None

                if any(p.match(fun) for p in EXCLUDED_PATTERNS):
                    return None

                job_duration = 0
                job_return = job_result.get('return')
                if isinstance(job_return, dict):
                    for val in job_return.values():
                        if isinstance(val, dict):
                            job_duration += val.get('duration', 0)

                return {
                    'job_duration': {
                        'master': MASTER_HOSTNAME,
                        'minion': minion,
                        'jid': last_job,
                        'fun': fun,
                        'value': job_duration / 1000
                    },
                    'job_retcode': {
                        'master': MASTER_HOSTNAME,
                        'minion': minion,
                        'fun': fun,
                        'value': job_result.get('retcode')
                    }
                }

            log.info('Preparing jobs metrics...')
            with ThreadPoolExecutor(max_workers=25) as executor:
                futures = {executor.submit(process_minion, minion): minion for minion in all_minions}
                job_durations = []
                job_retcodes = []
                for future in as_completed(futures):
                    try:
                        result = future.result()
                        if result:
                            job_durations.append(result['job_duration'])
                            job_retcodes.append(result['job_retcode'])
                    except Exception:
                        log.error(f'Error processing minion {futures[future]}: {traceback.format_exc()}')
                metrics['salt_minion_job_duration_seconds'].extend(job_durations)
                metrics['salt_minion_job_retcode'].extend(job_retcodes)
            log.info('Prepared.')

            log.info('All data collected and prepared successfully!')
            metrics.update({
                'salt_all_jobs_total': {'value': len(job_list)},
                'salt_active_jobs_total': {'value': len(active_jobs_list)},
                'salt_minions_up_total': {'value': len(minions_up)},
                'salt_minions_down_total': {'value': len(minions_down)},
                'salt_minions_total': {'value': len(all_minions)},
                'salt_accepted_minions_total': {'value': len(key_data.get('minions', []))},
                'salt_denied_minions_total': {'value': len(key_data.get('minions_denied', []))},
                'salt_rejected_minions_total': {'value': len(key_data.get('minions_rejected', []))},
                'salt_unaccepted_minions_total': {'value': len(key_data.get('minions_pre', []))}
            })

            del minion_statuses, job_list, active_jobs_list, minions_up, minions_down, all_minions, key_data, down_metrics, up_metrics

        except Exception:
            log.error(f'Something went wrong when trying to collect and prepare metrics data: {traceback.format_exc()}')
            if self.current_metrics:
                metrics.clear()
                metrics.update(self.current_metrics)

        if EXPORTER_DEBUG:
            log.info(f'Collected metrics: {metrics}')

        if self.current_metrics:
            self.current_metrics.clear()
        self.current_metrics.update(metrics)
        del metrics

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
                if metric['type'] == 'not_labeled':
                    _set_or_observe(metric['metric'], value['value'])
                else:
                    if metric['mode'] == 'single':
                        metric['metric'].clear()
                    for metric_data in value:
                        _set_or_observe(metric['metric'].labels(**{k: v for k, v in metric_data.items() if k not in ('value')}), metric_data['value'])
                del metric
            log.info('Metrics updated.')
        except Exception:
            log.error(f'Something went wrong when trying to update metrics data: {traceback.format_exc()}')
        del counts

    def send_data_to_main(self, counts: dict):
        _headers = {
            "Content-Type": "application/json",
            "User-Agent": f"slave_master_{socket.gethostname().split('.')[0]}"
        }
        try:
            response = requests.post(f'http://{EXPORTER_MAIN_MASTER_ADDR}:{EXPORTER_RECEIVER_PORT}', headers=_headers, data=json.dumps(counts), verify=False)
            log.info(f'Data sent to main master server {EXPORTER_MAIN_MASTER_ADDR}, response: {response.status_code} - {response.text}')
            del counts, response
        except Exception:
            log.error(f'Something went wrong when trying to sent metrics data to main master server: {traceback.format_exc()}')

    def merge_metrics(self, main: dict, received: dict):
        merged = main.copy()

        for key in ['salt_active_jobs_total', 'salt_all_jobs_total', 'salt_minions_down_total']:
            val1 = merged.get(key, {}).get('value', 0)
            val2 = received.get(key, {}).get('value', 0)
            merged[key] = {'value': val1 + val2}

        total_minions = merged.get('salt_minions_total', {}).get('value', 0)
        minions_up_1 = merged.get('salt_minions_up_total', {}).get('value', 0)
        minions_up_2 = received.get('salt_minions_up_total', {}).get('value', 0)

        merged['salt_minions_down_total'] = {
            'value': total_minions - (minions_up_1 + minions_up_2)
        }

        merged['salt_minions_up_total'] = {
            'value': minions_up_1 + minions_up_2
        }

        merged['salt_minion_job_duration_seconds'].extend(received['salt_minion_job_duration_seconds'])
        merged['salt_minion_job_retcode'].extend(received['salt_minion_job_retcode'])

        main_status_map = {
            item['minion']: {'value': item['value']}
            for item in merged.get('salt_minion_status', [])
        }
        counts_status_map = {
            item['minion']: {'value': item['value']}
            for item in received.get('salt_minion_status', [])
        }

        for minion, counts_data in counts_status_map.items():
            main_data = main_status_map.get(minion)
            if main_data and counts_data['value'] != main_data['value']:
                main_status_map[minion]['value'] = 1

        merged['salt_minion_status'] = [
            {'minion': minion, 'value': data['value']}
            for minion, data in main_status_map.items()
        ]

        del main_status_map, counts_status_map, received, main
        return merged

    async def run(self, addr: str = None, port: int = None, delay: int = None):
        addr = addr or EXPORTER_ADDR
        port = port or EXPORTER_PORT
        delay = delay or EXPORTER_COLLECT_DELAY

        if EXPORTER_MAIN_MASTER:
            prom.start_http_server(port, addr=addr)
            log.info(f"Exporter started on {addr}:{port}")

        async def run_metrics_collector(delay):
            while True:
                self.collect_data()
                if not EXPORTER_MULTIMASTER_ENABLED:
                    self.update_metrics(self.current_metrics)
                else:
                    if EXPORTER_MAIN_MASTER:
                        if self.received_metrics:
                            self.update_metrics(self.merge_metrics(self.current_metrics, self.received_metrics))
                        else:
                            self.update_metrics(self.current_metrics)
                    else:
                        main_reachable = check_reachable(EXPORTER_MAIN_MASTER_ADDR)
                        port_open = check_port(EXPORTER_MAIN_MASTER_ADDR, EXPORTER_RECEIVER_PORT)

                        if main_reachable and port_open:
                            self.send_data_to_main(self.current_metrics)
                        else:
                            self.update_metrics(self.current_metrics)
                if EXPORTER_DEBUG:
                    snapshot = tracemalloc.take_snapshot()
                    display_top(snapshot)
                    del snapshot
                await asyncio.sleep(delay)

        thread = Thread(target=lambda: asyncio.run(run_metrics_collector(delay)), daemon=True)
        thread.start()
        while thread.is_alive():
            await asyncio.sleep(1)

    async def run_receiver(self, addr: str = None, port: int = None):

        def create_flask_app():
            app = Flask(__name__)

            @app.route('/', methods=['POST'])
            def _handle_post():
                data = request.get_json()
                if not data:
                    del data
                    return jsonify({'error': 'No JSON received'}), 400
                if sorted(data.keys()) != sorted(self.METRICS_INFO.keys()):
                    del data
                    return jsonify({'error': 'Invalid metric data provided'}), 400
                if self.received_metrics:
                    self.received_metrics.clear()
                self.received_metrics.update(data)
                if self.current_metrics:
                    self.update_metrics(self.merge_metrics(self.current_metrics, self.received_metrics))

                del data
                return jsonify({'success': 'Data added successfully'}), 200

            return app

        addr = addr or EXPORTER_ADDR
        port = port or EXPORTER_RECEIVER_PORT

        app = create_flask_app()

        def run_flask():
            app.run(host=addr, port=port)

        thread = Thread(target=run_flask, daemon=True)
        thread.start()
        log.info(f"Receiver server started on {addr}:{port}")
        while thread.is_alive():
            await asyncio.sleep(1)


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
            '--rport',
            type=int,
            help='The port where the receiver server will operate.'
        )
        parser.add_argument(
            '--delay',
            type=int,
            help='The delay between metrics updates.'
        )
        args = parser.parse_args()
        log.info('===========================Startup info===========================')
        log.info(f'SaltStack Metrics Exporter v.{__version__}')
        log.info(f'Metrics server addr: {EXPORTER_ADDR}')
        log.info(f'Metrics server port: tcp/{EXPORTER_PORT}')
        log.info(f'Receiver server port: tcp/{EXPORTER_RECEIVER_PORT}')
        log.info(f'Collect delay: {EXPORTER_COLLECT_DELAY} seconds')
        log.info(f'Debug enabled: {EXPORTER_DEBUG}')
        log.info(f'Is it main master?: {EXPORTER_MAIN_MASTER}')
        log.info(f'Main master addr: {EXPORTER_MAIN_MASTER_ADDR}')
        log.info(f'Main master reachable: {check_reachable(EXPORTER_MAIN_MASTER_ADDR)}')
        log.info(f'Multimaster mode enabled: {EXPORTER_MULTIMASTER_ENABLED}')
        log.info(f'Included functions: {EXPORTER_INCLUDED_FUNCTIONS}')
        log.info(f'Excluded functions: {EXPORTER_EXCLUDED_FUNCTIONS}')
        log.info('==================================================================')
        exporter = SaltMetricsExporter()
        loop = asyncio.get_event_loop()
        tasks = []
        if EXPORTER_MAIN_MASTER and EXPORTER_MULTIMASTER_ENABLED:
            tasks.append(exporter.run_receiver(args.addr, args.rport))
        tasks.append(exporter.run(args.addr, args.port, args.delay))
        if EXPORTER_DEBUG:
            tracemalloc.start()
        loop.run_until_complete(asyncio.gather(*tasks))
    except KeyboardInterrupt:
        log.info('Server stopped by user.')
