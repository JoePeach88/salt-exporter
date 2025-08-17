import os
import configparser


config = configparser.ConfigParser()
config_exists = False
if os.path.exists('./exporter'):
    config_exists = True
    config.read('./exporter')
EXPORTER_ADDR = os.getenv('EXPORTER_ADDR') if os.getenv('EXPORTER_ADDR') else (config.get('main', 'addr') if config_exists else '0.0.0.0')
EXPORTER_COLLECT_DELAY = os.getenv('EXPORTER_COLLECT_DELAY') if os.getenv('EXPORTER_COLLECT_DELAY') else (config.get('main', 'collect_delay') if config_exists else 300)
EXPORTER_PORT = os.getenv('EXPORTER_PORT') if os.getenv('EXPORTER_PORT') else (config.get('main', 'port') if config_exists else 9111)
