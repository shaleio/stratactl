from strata.aws import session as aws_session
from strata.errors import StrataError
from strata.registry import jobs
from strata.runner import logs as runner_logs


def run(settings, args) -> int:
    sess = aws_session.make_session(settings)
    job = jobs.get(sess, settings.customer.jobs_table, args.job_id)
    if not job:
        raise StrataError(f"no such job: {args.job_id}")

    print(f"job {args.job_id}: {job.get('action')} {job.get('module_id')}@"
          f"{job.get('version')}  status={job.get('status')}")

    build_id = job.get("build_id")
    status = job.get("status")
    if build_id and status not in jobs.TERMINAL:
        # still running — tail live
        runner_logs.stream_build(sess, build_id)
        return 0

    loc = job.get("log_location")
    if loc and ":" in loc:
        group, stream = loc.split(":", 1)
        runner_logs.replay(sess, group, stream)
    else:
        print("(no log location recorded for this job)")
    return 0
