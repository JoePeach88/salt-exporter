import socket
import prometheus_client as prom
import logging
import sys
import importlib
import argparse
import traceback
import gc
import re
import json
import asyncio
import requests
import subprocess
from pathlib import Path
from env import *
from datetime import datetime
from flask import Flask, request, jsonify
from concurrent.futures import ThreadPoolExecutor, as_completed


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
from ..modules.salt_master_local_client import salt_runner, salt_key, salt_print_job, salt_list_jobs
__virtualname__ = "salt_master_metrics"
__version__ = '1.00'
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
            'type': prom.Gauge,
            'labels': ['master', 'minion', 'jid', 'fun']
        },
        'minion_job_retcode': {
            'name': 'salt_minion_job_retcode',
            'desc': 'Retcode of Salt job.',
            'type': prom.Gauge,
            'labels': ['master', 'minion', 'fun']
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
        self.current_metrics = {}
        self.received_metrics = {}

    def _create_metrics(self):
        log.info("Creating metrics...")
        metrics = {}
        try:
            for key, meta in self.METRICS_INFO.items():
                metric_type = meta.get('type', prom.Gauge)
                name = meta.get('name')
                desc = meta.get('desc')
                mode = meta.get('mode', 'single')
                labels = meta.get('labels', None)
                metrics.update({
                    key: {
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
            'minion_status': [],
            'minion_job_duration': [],
            'minion_job_retcode': []
        }
        try:
            log.info('Starting collecting data...')
            log.info('Collecting minion statuses...')
            minion_statuses = salt_runner.cmd('manage.status', print_event=EXPORTER_DEBUG)
            log.info('Collected.')
            log.info('Collecting jobs...')
            job_list = salt_list_jobs(start_time=datetime.today().replace(hour=0, minute=0, second=0).strftime("%Y, %b %d %H:%M:%S"),
                                      end_time=datetime.today().replace(hour=23, minute=59, second=59).strftime("%Y, %b %d %H:%M:%S")
                                      )
            job_list = dict(sorted(job_list.items(), reverse=True))
            log.info('Collected.')
            log.info('Collecting active jobs...')
            active_jobs_list = salt_runner.cmd('jobs.active', print_event=EXPORTER_DEBUG)
            log.info('Collected.')
            key_data = salt_key.list_keys()
            minions_up = minion_statuses.get('up', [])
            minions_down = minion_statuses.get('down', [])
            log.info('Preparing down minions metric...')
            for minion in minions_down:
                metrics['minion_status'].append({
                    'minion': minion,
                    'value': 0
                })
            log.info('Prepared.')
            log.info('Preparing up minions metric...')
            for minion in minions_up:
                metrics['minion_status'].append({
                    'minion': minion,
                    'value': 1
                })
            log.info('Prepared.')
            all_minions = minions_up + minions_down

            def process_minion(minion):
                last_job = [int(job_id) for job_id, details in job_list.items()
                            if details.get('Target') == minion and not any(re.match(p, details.get('Function')) for p in EXPORTER_EXCLUDED_FUNCTIONS) and any(re.match(p, details.get('Function')) for p in EXPORTER_INCLUDED_FUNCTIONS)]
                if not last_job:
                    return None

                last_job = max(last_job)
                last_job_details = salt_print_job(last_job)

                if not last_job_details:
                    return None

                job_data = last_job_details[next(iter(last_job_details))]
                fun = job_data.get('Function')
                job_result = job_data.get('Result', {}).get(minion, {})

                if not isinstance(job_result, dict) or not job_result:
                    return None

                if any(re.match(p, fun) for p in EXPORTER_EXCLUDED_FUNCTIONS):
                    return None

                job_duration = 0
                job_return = job_result.get('return')
                if isinstance(job_return, dict):
                    for job_data_val in job_return.values():
                        if isinstance(job_data_val, dict):
                            job_duration += job_data_val.get('duration', 0)

                master_hostname = socket.gethostname()

                return {
                    'job_duration': {
                        'master': master_hostname,
                        'minion': minion,
                        'jid': last_job,
                        'fun': fun,
                        'value': job_duration / 1000
                    },
                    'job_retcode': {
                        'master': master_hostname,
                        'minion': minion,
                        'fun': fun,
                        'value': job_result.get('retcode')
                    }
                }

            log.info('Preparing jobs metrics...')
            with ThreadPoolExecutor(max_workers=25) as executor:
                futures = {executor.submit(process_minion, minion): minion for minion in all_minions}
                for future in as_completed(futures):
                    result = future.result()
                    if result:
                        metrics['minion_job_duration'].append(result['job_duration'])
                        metrics['minion_job_retcode'].append(result['job_retcode'])
            log.info('Prepared.')
            log.info('All data collected and prepared successfully!')
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
        except Exception:
            log.error(f'Something went wrong when trying to collect and prepare metrics data: {traceback.format_exc()}')
            if self.current_metrics:
                metrics = self.current_metrics

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
                metric_type = metric['type']
                metric_mode = metric['mode']
                if metric_type == 'not_labeled':
                    _set_or_observe(metric_obj, value['value'])
                else:
                    if metric_mode == 'single':
                        metric_obj.clear()
                    for metric_data in value:
                        labels = {k: v for k, v in metric_data.items() if k not in ('value')}
                        val = metric_data['value']
                        labeled_metric = metric_obj.labels(**labels)
                        _set_or_observe(labeled_metric, val)
            log.info('Metrics updated.')
        except Exception:
            log.error(f'Something went wrong when trying to update metrics data: {traceback.format_exc()}')

    def send_data_to_main(self, counts: dict):
        _headers = {
            "Content-Type": "application/json",
            "User-Agent": f"slave_master_{socket.gethostname().split('.')[0]}"
        }
        try:
            response = requests.post(f'http://{EXPORTER_MAIN_MASTER_ADDR}:{EXPORTER_RECEIVER_PORT}', headers=_headers, data=json.dumps(counts), verify=False)
            log.info(f'Data sent to main master server {EXPORTER_MAIN_MASTER_ADDR}, response: {response.status_code} - {response.text}')
        except Exception:
            log.error(f'Something went wrong when trying to sent metrics data to main master server: {traceback.format_exc()}')

    def merge_metrics(self, main: dict, received: dict):
        merged = main.copy()

        for key in ['total_active_jobs', 'total_jobs', 'minions_down_count']:
            val1 = merged.get(key, {}).get('value', 0)
            val2 = received.get(key, {}).get('value', 0)
            merged[key] = {'value': val1 + val2}

        total_minions = merged.get('total_minions_count', {}).get('value', 0)
        minions_up_1 = merged.get('minions_up_count', {}).get('value', 0)
        minions_up_2 = received.get('minions_up_count', {}).get('value', 0)

        merged['minions_down_count'] = {
            'value': total_minions - (minions_up_1 + minions_up_2)
        }

        merged['minions_up_count'] = {
            'value': minions_up_1 + minions_up_2
        }

        merged['minion_job_duration'].extend(received['minion_job_duration'])
        merged['minion_job_retcode'].extend(received['minion_job_retcode'])

        main_status_map = {
            item['minion']: {'value': item['value']}
            for item in merged.get('minion_status', [])
        }
        counts_status_map = {
            item['minion']: {'value': item['value']}
            for item in received.get('minion_status', [])
        }

        for minion, counts_data in counts_status_map.items():
            main_data = main_status_map.get(minion)
            if main_data and counts_data['value'] != main_data['value']:
                main_status_map[minion]['value'] = 1

        merged['minion_status'] = [
            {'minion': minion, 'value': data['value']}
            for minion, data in main_status_map.items()
        ]
        return merged

    async def run(self, addr: str = None, port: int = None, delay: int = None):
        addr = addr or EXPORTER_ADDR
        port = port or EXPORTER_PORT
        delay = delay or EXPORTER_COLLECT_DELAY

        if EXPORTER_MAIN_MASTER and EXPORTER_MULTIMASTER_ENABLED:
            prom.start_http_server(port, addr=addr)
            log.info(f"Exporter started on {addr}:{port}")

        while True:
            counts = self.collect_data()
            if EXPORTER_MULTIMASTER_ENABLED:
                if EXPORTER_MAIN_MASTER:
                    metrics = counts
                    if self.received_metrics:
                        metrics = self.merge_metrics(metrics, self.received_metrics)
                        if not self.current_metrics:
                            self.current_metrics = counts
                    self.update_metrics(metrics)
                    self.current_metrics = counts
                else:
                    main_reachable = check_reachable(EXPORTER_MAIN_MASTER_ADDR)
                    port_open = check_port(EXPORTER_MAIN_MASTER_ADDR, EXPORTER_RECEIVER_PORT)
                    if main_reachable and port_open:
                        self.send_data_to_main(counts)
                    else:
                        self.update_metrics(counts)
            else:
                self.update_metrics(counts)

            await asyncio.sleep(delay)

    async def run_receiver(self, addr: str = None, port: int = None):

        def create_flask_app():
            app = Flask(__name__)

            @app.route('/', methods=['POST'])
            def handle_post():
                data = request.get_json()
                if not data:
                    return jsonify({'error': 'No JSON received'}), 400
                if sorted(data.keys()) != sorted(self.METRICS_INFO.keys()):
                    return jsonify({'error': 'Invalid metric data provided'}), 400

                self.received_metrics = data
                if self.current_metrics:
                    metrics = self.merge_metrics(self.current_metrics, self.received_metrics)
                    self.update_metrics(metrics)
                return jsonify({'success': 'Data added successfully'}), 200

            return app

        addr = addr or EXPORTER_ADDR
        port = port or EXPORTER_RECEIVER_PORT

        app = create_flask_app()

        from threading import Thread

        def run_flask():
            app.run(host=addr, port=port, debug=EXPORTER_DEBUG)

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
        loop.run_until_complete(asyncio.gather(*tasks))
    except KeyboardInterrupt:
        log.info('Server stopped by user.')
