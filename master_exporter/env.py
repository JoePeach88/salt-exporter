import os
import configparser
from pathlib import Path


config = configparser.ConfigParser()
config_exists = False
if os.path.exists(f'{Path(__file__).resolve().parent}/exporter'):
    config_exists = True
    config.read(f'{Path(__file__).resolve().parent}/exporter')
EXPORTER_ADDR = os.getenv('EXPORTER_ADDR') if os.getenv('EXPORTER_ADDR') else (config.get('main', 'addr') if config_exists and config.has_option('main', 'addr') else '0.0.0.0')
EXPORTER_COLLECT_DELAY = int(os.getenv('EXPORTER_COLLECT_DELAY')) if os.getenv('EXPORTER_COLLECT_DELAY') else (int(config.get('main', 'collect_delay')) if config_exists and config.has_option('main', 'collect_delay') else 300)
EXPORTER_PORT = int(os.getenv('EXPORTER_PORT')) if os.getenv('EXPORTER_PORT') else (int(config.get('main', 'port')) if config_exists and config.has_option('main', 'port') else 9111)
EXPORTER_RECIEVER_PORT = int(os.getenv('EXPORTER_RECIEVER_PORT')) if os.getenv('EXPORTER_RECIEVER_PORT') else (int(config.get('main', 'rport')) if config_exists and config.has_option('main', 'rport') else 9112)
EXPORTER_DEBUG = bool(os.getenv('EXPORTER_DEBUG')) if os.getenv('EXPORTER_DEBUG') else (bool(config.get('main', 'debug')) if config_exists and config.has_option('main', 'debug') else False)
EXPORTER_EXCLUDED_FUNCTIONS = os.getenv('EXPORTER_EXCLUDED_FUNCTIONS').split(',') if os.getenv('EXPORTER_EXCLUDED_FUNCTIONS') else (config.get('main', 'exclude_jobs').split(',') if config_exists and config.has_option('main', 'exclude_jobs') else 
[
    '^runner.*'
])
EXPORTER_MAIN_MASTER = bool(os.getenv('EXPORTER_MAIN_MASTER')) if os.getenv('EXPORTER_MAIN_MASTER') else (bool(config.get('main', 'main_master')) if config_exists and config.has_option('main', 'main_master') else True)
EXPORTER_MULTIMASTER_ENABLED = bool(os.getenv('EXPORTER_MULTIMASTER_ENABLED')) if os.getenv('EXPORTER_MULTIMASTER_ENABLED') else (bool(config.get('main', 'multimaster_mode')) if config_exists and config.has_option('main', 'multimaster_mode') else False)
