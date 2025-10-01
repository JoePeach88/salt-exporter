## Installation

1. Copy repo via:

```bash
git clone git@github.com:JoePeach88/salt-exporter.git
```

2. Prepare folder structure on target salt-master, this should be something like:

```text
opt
└──saltstack
   └── exporters
       └── salt-exporter
           ├   modules
           ├   └── salt_master_local_client.py
           ├── env.py
           ├── exporter
           └── exporter.py
```

3. Check that required ports are opened on firewall, for default configuration it\`s: `9111`

4. Install requirements via pip:

```bash
/opt/saltstack/salt/bin/python3 -m pip install -r /path/to/requirements.txt
```

5. Copy unit file from downloaded repo to: `/etc/systemd/system/salt-master-exporter.service`

6. Reload daemon, enable and start unit:

```bash
systemctl daemon-reload && systemctl enable --now salt-master-exporter.service
```

## Configuration

1. Go to config file: `/opt/saltstack/exporters/salt-exporter/exporter`

2. Edit configuration:

```ini
[main]
addr=
collect_delay=
port=
rport=
main_master=
main_master_addr=
multimaster_mode=
debug=
exclude_jobs=
include_jobs=
```

- `addr` - The address where the server will operate (default: `0.0.0.0`).

- `collect_delay` - The delay between metrics updates (default: `300`).

- `port` - The port where the server will operate (default: `9111`).

- `rport` - The port where the receiver server will operate (default: `9112`).

- `main_master` -  Indicates whether this server, where the exporter is running, is the main master (default: `True`).

- `main_master_addr` - The address of main master.

- `multimaster_mode` - Enables multimaster mode for the exporter (default: `False`).

- `debug` - Launch exporter in debug mode (default: `False`).

- `exclude_jobs` - Which jobs excluded from parse in duration and retcode (supports regex).

- `include_jobs` -  - Which jobs included for parse in duration and retcode (supports regex).

### Configuration for single master/multiple masters with syndic

```ini
[main]
addr=0.0.0.0
collect_delay=300
port=9111
rport=9112
main_master=True
main_master_addr=localhost
multimaster_mode=False
debug=False
exclude_jobs=
include_jobs=^state\..*
```

### Configuration for multiple masters (without syndic)

**Main master:**

```ini
[main]
addr=0.0.0.0
collect_delay=300
port=9111
rport=9112
main_master=True
main_master_addr=prod-main-salt-master.local.domain
multimaster_mode=True
debug=False
exclude_jobs=
include_jobs=^state\..*
```

**Other masters:**

```ini
[main]
addr=0.0.0.0
collect_delay=300
port=9111
rport=9112
main_master=False
main_master_addr=prod-main-salt-master.local.domain
multimaster_mode=True
debug=False
exclude_jobs=
include_jobs=^state\..*
```

## Preview

When open page with metrics (via `curl` or something else), you`ll see output like this:

```text
...
# HELP salt_all_jobs_total Total jobs count for current date.
# TYPE salt_all_jobs_total gauge
salt_all_jobs_total 1365.0
# HELP salt_active_jobs_total Total active jobs count.
# TYPE salt_active_jobs_total gauge
salt_active_jobs_total 0.0
# HELP salt_minions_up_total Total up minions count.
# TYPE salt_minions_up_total gauge
salt_minions_up_total 3.0
# HELP salt_minions_down_total Total down minions count.
# TYPE salt_minions_down_total gauge
salt_minions_down_total 2.0
# HELP salt_minions_total Total minions count.
# TYPE salt_minions_total gauge
salt_minions_total 6.0
# HELP salt_accepted_minions_total Total accepted minions count.
# TYPE salt_accepted_minions_total gauge
salt_accepted_minions_total 5.0
# HELP salt_denied_minions_total Total denied minions count.
# TYPE salt_denied_minions_total gauge
salt_denied_minions_total 1.0
# HELP salt_rejected_minions_total Total rejected minions count.
# TYPE salt_rejected_minions_total gauge
salt_rejected_minions_total 0.0
# HELP salt_unaccepted_minions_total Total unaccepted minions count.
# TYPE salt_unaccepted_minions_total gauge
salt_unaccepted_minions_total 0.0
# HELP salt_minion_job_duration_seconds Duration of Salt jobs in seconds.
# TYPE salt_minion_job_duration_seconds gauge
salt_minion_job_duration_seconds{fun="state.apply",jid="20250908133244084128",master="master1",minion="minion1"} 2.022126
salt_minion_job_duration_seconds{fun="state.apply",jid="20250908133245525956",master="master1",minion="minion2"} 2.4806880000000002
salt_minion_job_duration_seconds{fun="state.highstate",jid="20250908135216842648",master="master2",minion="minion3"} 0.0
salt_minion_job_duration_seconds{fun="state.apply",jid="20250908132918050527",master="master2",minion="minion4"} 1.9179059999999999
salt_minion_job_duration_seconds{fun="saltutil.refresh_pillar",jid="20250908135703802637",master="master2",minion="minion5"} 0.0
# HELP salt_minion_job_retcode Retcode of Salt job.
# TYPE salt_minion_job_retcode gauge
salt_minion_job_retcode{fun="state.apply",master="master1",minion="minion1"} 1.0
salt_minion_job_retcode{fun="state.apply",master="master1",minion="minion2"} 1.0
salt_minion_job_retcode{fun="state.highstate",master="master2",minion="minion3"} 1.0
salt_minion_job_retcode{fun="state.apply",master="master2",minion="minion4"} 0.0
salt_minion_job_retcode{fun="saltutil.refresh_pillar",master="master2",minion="minion5"} 0.0
# HELP salt_minion_status Status of salt-minion (0 - offline, 1 - online).
# TYPE salt_minion_status gauge
salt_minion_status{minion="minion1"} 0.0
salt_minion_status{minion="minion2"} 0.0
salt_minion_status{minion="minion3"} 1.0
salt_minion_status{minion="minion4"} 1.0
salt_minion_status{minion="minion5"} 1.0
...
```
