# SaltStack Metrics Exporter

This exporter scraping data from salt-masters.

## Metrics

- `salt_all_jobs_total` - Total jobs count.
- `salt_active_jobs_total` - Total active jobs count.
- `salt_minions_up_total` - Total up minions count.
- `salt_minions_down_total` - Total down minions count.
- `salt_minions_total` - Total minions count.
- `salt_accepted_minions_total` - Total accepted minions count.
- `salt_denied_minions_total` - Total denied minions count.
- `salt_rejected_minions_total` - Total rejected minions count.
- `salt_unaccepted_minions_total` - Total unaccepted minions count.

## Install

1. Copy repo via:

```bash
git clone git@github.com:JoePeach88/salt-exporter.git
```

2. Prepare folder structure on target salt-master, this should be something like:

```text
opt
└──saltstack
   └── exporters
       ├   modules
       ├   └── salt_master_local_client.py
       └── master_exporter
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
systemctl daemon-reload && systemctl enable --now salt-metrics-exporter.service
```


## Configuration

1. Go to config file: `/opt/saltstack/exporters/master_exporter/exporter`

2. Edit configuration:

```ini
[main]
addr=
port=
collect_delay=
```

- `addr` - The address where the server will operate (default: `0.0.0.0`).

- `port` - The port where the server will operate (default: `9111`).

- `delay` - The delay between metrics updates (default: `300`).
