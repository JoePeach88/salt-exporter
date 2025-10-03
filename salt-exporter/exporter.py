import socket
import prometheus_client as prom
from prometheus_client.core import GaugeMetricFamily, Collector
import logging
import sys
import argparse
import traceback
import json
import requests
import subprocess
from env import *
from datetime import datetime
from flask import Flask, request, jsonify
from threading import Thread
from concurrent.futures import ThreadPoolExecutor, as_completed
from modules.salt_master_local_client import salt_runner, salt_client, salt_key, salt_print_job, salt_list_jobs, master_version


log = logging.getLogger(__name__)
formatter = logging.Formatter(fmt="%(message)s")
handler = logging.StreamHandler(stream=sys.stdout)
handler.setFormatter(formatter)
log.addHandler(handler)


def check_reachable(host: str):
    return subprocess.call(f"ping -c 1 {host}".split(' '), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) == 0

def check_port(host: str, port: int):
    return subprocess.call(f"nc -zv {host} {port}".split(' '), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) == 0
    

class SaltCollector(Collector):
    def __init__(self):
        # Эти данные нужны только для сбора, не для хранения состояния
        self.master_hostname = MASTER_HOSTNAME
        self.excluded_patterns = EXCLUDED_PATTERNS
        self.included_patterns = INCLUDED_PATTERNS

    def _get_minion_statuses(self):
        minion_statuses_return = salt_client.cmd('*', 'test.ping', full_return=True)
        up, down = [], []
        for m, result in minion_statuses_return.items():
            if result and 'ret' in result:
                up.append(m)
            else:
                down.append(m)
        return up, down

    def _get_job_list(self):
        start_time = datetime.today().replace(hour=0, minute=0, second=0).strftime("%Y, %b %d %H:%M:%S")
        end_time = datetime.today().replace(hour=23, minute=59, second=59).strftime("%Y, %b %d %H:%M:%S")
        job_list = salt_list_jobs(start_time=start_time, end_time=end_time)
        return dict(sorted(job_list.items(), reverse=True))

    def _process_minion(self, minion, job_list):
        matching_job_ids = (
            int(job_id) for job_id, details in job_list.items()
            if details.get('Target') == minion
            and not any(p.match(details.get('Function', '')) for p in self.excluded_patterns)
            and any(p.match(details.get('Function', '')) for p in self.included_patterns)
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

        if any(p.match(fun) for p in self.excluded_patterns):
            return None

        job_duration = 0
        job_return = job_result.get('return')
        if isinstance(job_return, dict):
            for val in job_return.values():
                if isinstance(val, dict):
                    job_duration += val.get('duration', 0)

        return {
            'job_duration': {
                'master': self.master_hostname,
                'minion': minion,
                'jid': str(last_job),
                'fun': fun,
                'value': job_duration / 1000
            },
            'job_retcode': {
                'master': self.master_hostname,
                'minion': minion,
                'fun': fun,
                'value': job_result.get('retcode')
            }
        }

    def collect(self):
        try:
            log.info('Starting to collect Salt metrics...')

            up_minions, down_minions = self._get_minion_statuses()
            all_minions = up_minions + down_minions

            job_list = self._get_job_list()
            active_jobs_list = salt_runner.cmd('jobs.active', print_event=EXPORTER_DEBUG)
            minion_versions = salt_client.cmd('*', 'test.version', full_return=True)
            key_data = salt_key.list_keys()

            yield GaugeMetricFamily('salt_all_jobs_total', 'Total jobs count for current date.', value=len(job_list))
            yield GaugeMetricFamily('salt_active_jobs_total', 'Total active jobs count.', value=len(active_jobs_list))
            yield GaugeMetricFamily('salt_minions_up_total', 'Total up minions count.', value=len(up_minions))
            yield GaugeMetricFamily('salt_minions_down_total', 'Total down minions count.', value=len(down_minions))
            yield GaugeMetricFamily('salt_minions_total', 'Total minions count.', value=len(all_minions))
            yield GaugeMetricFamily('salt_accepted_minions_total', 'Total accepted minions count.', value=len(key_data.get('minions', [])))
            yield GaugeMetricFamily('salt_denied_minions_total', 'Total denied minions count.', value=len(key_data.get('minions_denied', [])))
            yield GaugeMetricFamily('salt_rejected_minions_total', 'Total rejected minions count.', value=len(key_data.get('minions_rejected', [])))
            yield GaugeMetricFamily('salt_unaccepted_minions_total', 'Total unaccepted minions count.', value=len(key_data.get('minions_pre', [])))

            g_status = GaugeMetricFamily('salt_minion_status', 'Status of salt-minion (0 - offline, 1 - online).', labels=['minion'])
            for m in down_minions:
                g_status.add_metric([m], 0)
            for m in up_minions:
                g_status.add_metric([m], 1)
            yield g_status

            g_version = GaugeMetricFamily('salt_minion_version', 'Version of salt-minion.', labels=['minion', 'version'])
            for m, result in minion_versions.items():
                if result and 'ret' in result:
                    g_version.add_metric([m, str(result['ret'])], 1)
            yield g_version

            g_master_version = GaugeMetricFamily('salt_master_version', 'Version of salt-master.', labels=['master', 'version'])
            g_master_version.add_metric([self.master_hostname, str(master_version)], 1)
            yield g_master_version

            g_duration = GaugeMetricFamily(
                'salt_minion_job_duration_seconds',
                'Duration of Salt jobs in seconds.',
                labels=['master', 'minion', 'jid', 'fun']
            )
            g_retcode = GaugeMetricFamily(
                'salt_minion_job_retcode',
                'Retcode of Salt job.',
                labels=['master', 'minion', 'fun']
            )

            with ThreadPoolExecutor(max_workers=25) as executor:
                futures = {executor.submit(self._process_minion, minion, job_list): minion for minion in all_minions}
                for future in futures:
                    try:
                        result = future.result()
                        if result:
                            dur = result['job_duration']
                            g_duration.add_metric(
                                [dur['master'], dur['minion'], dur['jid'], dur['fun']],
                                dur['value']
                            )
                            ret = result['job_retcode']
                            g_retcode.add_metric(
                                [ret['master'], ret['minion'], ret['fun']],
                                ret['value'] if ret['value'] is not None else 0
                            )
                    except Exception as e:
                        log.error(f'Error processing minion {futures[future]}: {e}')

            yield g_duration
            yield g_retcode

            log.info('Salt metrics collected successfully.')

        except Exception as e:
            log.error(f'Error in SaltCollector.collect(): {traceback.format_exc()}')
            # В случае ошибки не возвращаем ничего, чтобы не сломать /metrics
            return


class SaltExporterManager:
    def __init__(self):
        self.collector = SaltCollector()
        self.received_metrics = {}  # Для multimaster режима

    def send_data_to_main(self, counts: dict):
        _headers = {
            "Content-Type": "application/json",
            "User-Agent": f"slave_master_{socket.gethostname().split('.')[0]}"
        }
        try:
            response = requests.post(
                f'http://{EXPORTER_MAIN_MASTER_ADDR}:{EXPORTER_RECEIVER_PORT}',
                headers=_headers,
                data=json.dumps(counts),
                verify=False
            )
            log.info(f'Data sent to main master server {EXPORTER_MAIN_MASTER_ADDR}, response: {response.status_code}')
        except Exception as e:
            log.error(f'Failed to send data to main master: {e}')

    def create_flask_app(self):
        app = Flask(__name__)

        @app.route('/', methods=['POST'])
        def _handle_post():
            data = request.get_json()
            if not data:
                return jsonify({'error': 'No JSON received'}), 400
            # Простая проверка структуры
            expected_keys = {'salt_minion_status', 'salt_minion_job_duration_seconds', ...}
            if not isinstance(data, dict):
                return jsonify({'error': 'Invalid data'}), 400

            self.received_metrics.clear()
            self.received_metrics.update(data)
            return jsonify({'success': 'Data received'}), 200

        return app

    async def run(self, addr: str = None, port: int = None, delay: int = None):
        addr = addr or EXPORTER_ADDR
        port = port or EXPORTER_PORT

        prom.REGISTRY.register(self.collector)
        prom.start_http_server(port, addr=addr)
        log.info(f"Exporter (with custom Collector) started on {addr}:{port}")

        if EXPORTER_MULTIMASTER_ENABLED and not EXPORTER_MAIN_MASTER:
            import asyncio
            while True:
                # Здесь нужно собрать данные так же, как в collect(), но в dict-формате для отправки
                # Для простоты можно временно вызвать внутренние методы коллектора или дублировать логику
                # В идеале, стоит вынести логику сбора в отдельный модуль, доступный и для Collector, и для отправки.
                log.info("Data collection for sending is not implemented in this example. Implement as needed.")
                await asyncio.sleep(delay or EXPORTER_COLLECT_DELAY)

        # Иначе просто ждём (сервер уже запущен в фоне)
        import asyncio
        while True:
            await asyncio.sleep(3600)  # Просто держим процесс живым

    async def run_receiver(self, addr: str = None, port: int = None):
        addr = addr or EXPORTER_ADDR
        port = port or EXPORTER_RECEIVER_PORT

        app = self.create_flask_app()
        thread = Thread(target=lambda: app.run(host=addr, port=port, debug=False), daemon=True)
        thread.start()
        log.info(f"Receiver server started on {addr}:{port}")
        import asyncio
        while thread.is_alive():
            await asyncio.sleep(1)

if __name__ == '__main__':
    try:
        parser = argparse.ArgumentParser()
        parser.add_argument('--addr', help='The address where the server will operate.')
        parser.add_argument('--port', type=int, help='The port where the server will operate.')
        parser.add_argument('--rport', type=int, help='The port where the receiver server will operate.')
        parser.add_argument('--delay', type=int, help='The delay between metrics updates.')
        args = parser.parse_args()

        log.info('===========================Startup info===========================')
        log.info(f'SaltStack Metrics Exporter v.{__version__}')
        log.info(f'Metrics server addr: {EXPORTER_ADDR}')
        log.info(f'Metrics server port: tcp/{EXPORTER_PORT}')
        log.info(f'Receiver server port: tcp/{EXPORTER_RECEIVER_PORT}')
        log.info(f'Debug enabled: {EXPORTER_DEBUG}')
        log.info(f'Is it main master?: {EXPORTER_MAIN_MASTER}')
        log.info(f'Main master addr: {EXPORTER_MAIN_MASTER_ADDR}')
        log.info(f'Multimaster mode enabled: {EXPORTER_MULTIMASTER_ENABLED}')
        log.info('==================================================================')

        manager = SaltExporterManager()
        import asyncio
        loop = asyncio.get_event_loop()
        tasks = []
        if EXPORTER_MAIN_MASTER and EXPORTER_MULTIMASTER_ENABLED:
            tasks.append(manager.run_receiver(args.addr, args.rport))
        tasks.append(manager.run(args.addr, args.port, args.delay))
        loop.run_until_complete(asyncio.gather(*tasks))

    except KeyboardInterrupt:
        log.info('Server stopped by user.')
