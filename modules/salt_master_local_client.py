import salt.config
import salt.key
import salt.loader
import salt.runner


master_config = salt.config.master_config('/etc/salt/master')
minion_config = salt.config.minion_config('/etc/salt/minion')
grains = salt.loader.grains(master_config)
master_config['grains'] = grains
pillars = salt.pillar.get_pillar(master_config, grains, minion_config['id'], 'base').compile_pillar()
utils = salt.loader.utils(master_config)
modules = salt.loader.minion_mods(master_config, utils=utils)
salt_runner = salt.runner.RunnerClient(master_config)
salt_key = salt.key.Key(master_config)
