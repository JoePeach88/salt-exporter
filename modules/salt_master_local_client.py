import salt.config
import salt.key
import salt.loader
import salt.runner
import salt.minion
import salt.utils.jid

try:
    import dateutil.parser as dateutil_parser

    DATEUTIL_SUPPORT = True
except ImportError:
    DATEUTIL_SUPPORT = False

master_config = salt.config.master_config('/etc/salt/master')
minion_config = salt.config.minion_config('/etc/salt/minion')
grains = salt.loader.grains(master_config)
master_config['grains'] = grains
pillars = salt.pillar.get_pillar(master_config, grains, minion_config['id'], 'base').compile_pillar()
utils = salt.loader.utils(master_config)
modules = salt.loader.minion_mods(master_config, utils=utils)
salt_runner = salt.runner.RunnerClient(master_config)
salt_key = salt.key.Key(master_config)
mminion = salt.minion.MasterMinion(master_config)


def _get_returner(returner_types):
    for returner in returner_types:
        if returner:
            return returner
    return None


def _format_job_instance(job):
    if not job:
        return {"Error": "Cannot contact returner or no job with this jid"}

    ret = {
        "Function": job.get("fun", "unknown-function"),
        "Arguments": list(job.get("arg", [])),
        "Target": job.get("tgt", "unknown-target"),
        "Target-type": job.get("tgt_type", "list"),
        "User": job.get("user", "root"),
    }

    metadata = job.get("metadata") or job.get("kwargs", {}).get("metadata")
    if metadata is not None:
        ret["Metadata"] = metadata

    if "Minions" in job:
        ret["Minions"] = job["Minions"]

    return ret


def _format_jid_instance(jid, job):
    ret = _format_job_instance(job)
    ret["StartTime"] = salt.utils.jid.jid_to_time(jid)
    return ret


def salt_print_job(jid):
    ret = {}
    returner = _get_returner((
        master_config.get("ext_job_cache"),
        None,
        master_config.get("master_job_cache")
    ))

    try:
        get_load_func = mminion.returners.get(f"{returner}.get_load")
        if not get_load_func:
            raise TypeError(f"Returner '{returner}.get_load' is not available.")

        job = get_load_func(jid)
        ret[jid] = _format_jid_instance(jid, job)

    except TypeError:
        return {jid: {"Result": (
            f"Requested returner {returner} is not available. Jobs cannot be "
            f"retrieved. Check master log for details."
        )}}

    get_jid_func = mminion.returners.get(f"{returner}.get_jid")
    if get_jid_func:
        ret[jid]["Result"] = get_jid_func(jid)
    else:
        ret[jid]["Result"] = None

    if master_config.get("job_cache_store_endtime"):
        get_endtime_func = mminion.returners.get(f"{master_config['master_job_cache']}.get_endtime")
        if get_endtime_func:
            endtime = get_endtime_func(jid)
            if endtime:
                ret[jid]["EndTime"] = endtime

    return ret


def salt_list_jobs(start_time, end_time):
    returner = _get_returner(
        (master_config["ext_job_cache"], None, master_config["master_job_cache"])
    )

    ret = mminion.returners[f"{returner}.get_jids"]()

    mret = {}
    for item in ret:
        _match = True

        if start_time and _match:
            _match = False
            if DATEUTIL_SUPPORT:
                parsed_start_time = dateutil_parser.parse(start_time)
                _start_time = dateutil_parser.parse(ret[item]["StartTime"])
                if _start_time >= parsed_start_time:
                    _match = True

        if end_time and _match:
            _match = False
            if DATEUTIL_SUPPORT:
                parsed_end_time = dateutil_parser.parse(end_time)
                _start_time = dateutil_parser.parse(ret[item]["StartTime"])
                if _start_time <= parsed_end_time:
                    _match = True

        if _match:
            mret[item] = ret[item]

    return mret
