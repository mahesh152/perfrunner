import sys
from time import sleep

from celery import Celery
from fabric import state
from fabric.api import cd, local, quiet, run, settings
from kombu import Queue
from logger import logger
from sqlalchemy import create_engine

from perfrunner import celerylocal, celeryremote
from perfrunner.helpers.misc import log_phase
from perfrunner.settings import REPO
from perfrunner.workloads.pillowfight import Pillowfight
from spring.wgen import WorkloadGen

celery = Celery('workers')
if '--remote' in sys.argv or '-C' in sys.argv:
    # -C flag is a hack to distinguish local and remote workers!
    celery.config_from_object(celeryremote)
else:
    celery.config_from_object(celerylocal)


@celery.task
def task_run_workload(settings, target, timer):
    wg = WorkloadGen(settings, target, timer=timer)
    wg.run()


@celery.task
def run_pillowfight_via_celery(settings, target, timer):
    """Run a given workload using pillowfight rather than spring.

       Method must be declared in the worker module so that when a
       perfrunner instance is created on a client machine the celery
       task is declared in scope"""
    host, port = target.node.split(':')

    pillow = Pillowfight(host=host, port=port, bucket=target.bucket,
                         password=target.password,
                         num_items=settings.items,
                         num_threads=settings.workers,
                         writes=settings.updates, size=settings.size)
    pillow.run()


class WorkerManager(object):

    def __new__(cls, *args, **kwargs):
        if '--remote' in sys.argv:
            return RemoteWorkerManager(*args, **kwargs)
        else:
            return LocalWorkerManager(*args, **kwargs)


class RemoteWorkerManager(object):

    RACE_DELAY = 2

    def __init__(self, cluster_spec, test_config):
        self.cluster_spec = cluster_spec
        self.buckets = test_config.buckets

        self.reuse_worker = test_config.worker_settings.reuse_worker
        self.temp_dir = test_config.worker_settings.worker_dir
        logger.info("Using prefix for temp_dir (worker_dir): {}".format(self.temp_dir))
        self.user, self.password = cluster_spec.client_credentials
        with settings(user=self.user, password=self.password):
            self.initialize_project()
            self.start()

    def initialize_project(self):
        for worker, master in zip(self.cluster_spec.workers,
                                  self.cluster_spec.yield_masters()):
            state.env.host_string = worker
            run('killall -9 celery', quiet=True)
            for bucket in self.buckets:
                logger.info('Intializing remote worker environment')

                qname = '{}-{}'.format(master.split(':')[0], bucket)
                temp_dir = '{}-{}'.format(self.temp_dir, qname)

                r = run('test -d {}'.format(temp_dir), warn_only=True, quiet=True)
                if r.return_code == 0:
                    if self.reuse_worker == 'true':
                        return
                    logger.error('Worker env exists, but reuse not specified')
                    sys.exit(1)

                run('mkdir {}'.format(temp_dir))
                with cd(temp_dir):
                    run('git clone -q {}'.format(REPO))
                with cd('{}/perfrunner'.format(temp_dir)):
                    run('make')

    def start(self):
        for worker, master in zip(self.cluster_spec.workers,
                                  self.cluster_spec.yield_masters()):
            state.env.host_string = worker
            for bucket in self.buckets:
                qname = '{}-{}'.format(master.split(':')[0], bucket)
                logger.info('Starting remote Celery worker: {}'.format(qname))

                temp_dir = '{}-{}/perfrunner'.format(self.temp_dir, qname)
                run('cd {0}; ulimit -n 10240; '
                    'PYTHONOPTIMIZE=1 C_FORCE_ROOT=1 '
                    'nohup env/bin/celery worker '
                    '-A perfrunner.helpers.worker -Q {1} -c 1 -n {2} -C '
                    '&>/tmp/worker_{1}.log &'.format(temp_dir, qname, worker),
                    pty=False)

    def run_workload(self, settings, target_iterator, timer=None,
                     run_workload=task_run_workload):
        self.workers = []
        for target in target_iterator:
            log_phase('workload generator', settings)

            qname = '{}-{}'.format(target.node.split(':')[0], target.bucket)
            queue = Queue(name=qname)
            worker = run_workload.apply_async(
                args=(settings, target, timer),
                queue=queue.name, expires=timer,
            )
            self.workers.append(worker)
            sleep(self.RACE_DELAY)

    def wait_for_workers(self):
        logger.info('Waiting for workers to finish')
        for worker in self.workers:
            worker.wait()
        logger.info('All workers are done')

    def terminate(self):
        for worker, master in zip(self.cluster_spec.workers,
                                  self.cluster_spec.yield_masters()):
            state.env.host_string = worker
            for bucket in self.buckets:
                with settings(user=self.user, password=self.password):
                    logger.info('Terminating remote Celery worker')
                    run('killall -9 celery', quiet=True)

                    logger.info('Cleaning up remote worker environment')
                    qname = '{}-{}'.format(master.split(':')[0], bucket)
                    temp_dir = '{}-{}'.format(self.temp_dir, qname)
                    if self.reuse_worker == 'false':
                        run('rm -fr {}'.format(temp_dir))


class LocalWorkerManager(RemoteWorkerManager):

    SQLITE_DBS = ('/tmp/perfrunner.db', '/tmp/results.db')

    def __init__(self, cluster_spec, test_config):
        self.cluster_spec = cluster_spec
        self.buckets = test_config.buckets

        self.terminate()
        self.tune_sqlite()
        self.start()

    def tune_sqlite(self):
        for db in self.SQLITE_DBS:
            engine = create_engine('sqlite:///{}'.format(db))
            engine.execute('PRAGMA read_uncommitted=1;')
            engine.execute('PRAGMA synchronous=OFF;')

    def start(self):
        for master in self.cluster_spec.yield_masters():
            for bucket in self.buckets:
                qname = '{}-{}'.format(master.split(':')[0], bucket)
                logger.info('Starting local Celery worker: {}'.format(qname))
                local('PYTHONOPTIMIZE=1 C_FORCE_ROOT=1 '
                      'nohup env/bin/celery worker '
                      '-A perfrunner.helpers.worker -Q {0} -c 1 '
                      '>/tmp/worker_{0}.log &'.format(qname))
                sleep(self.RACE_DELAY)

    def terminate(self):
        logger.info('Terminating local Celery workers')
        with quiet():
            local('killall -9 celery')
            for db in self.SQLITE_DBS:
                local('rm -fr {}'.format(db))
