import os.path
import time
from random import uniform

from decorator import decorator
from fabric import state
from fabric.api import execute, get, parallel, put, run, settings
from fabric.exceptions import CommandTimeout
from logger import logger

from perfrunner.helpers.misc import uhex


@decorator
def all_hosts(task, *args, **kargs):
    self = args[0]
    return execute(parallel(task), *args, hosts=self.hosts, **kargs)


@decorator
def single_host(task, *args, **kargs):
    self = args[0]
    with settings(host_string=self.hosts[0]):
        return task(*args, **kargs)


@decorator
def single_client(task, *args, **kargs):
    self = args[0]
    with settings(host_string=self.cluster_spec.workers[0]):
        return task(*args, **kargs)


@decorator
def all_clients(task, *args, **kargs):
    self = args[0]
    return execute(parallel(task), *args, hosts=self.cluster_spec.workers, **kargs)


@decorator
def all_kv_nodes(task, *args, **kargs):
    self = args[0]
    self.host_index = 0
    return execute(parallel(task), *args, hosts=self.kv_hosts, **kargs)


@decorator
def kv_node(task, *args, **kargs):
    self = args[0]
    host = self.cluster_spec.yield_kv_servers().next()
    with settings(host_string=host):
        return task(*args, **kargs)


class RemoteHelper(object):

    def __new__(cls, cluster_spec, test_config, verbose=False):
        if not cluster_spec.ssh_credentials:
            return None

        state.env.user, state.env.password = cluster_spec.ssh_credentials
        state.output.running = verbose
        state.output.stdout = verbose

        os = cls.detect_os(cluster_spec)
        if os == 'Cygwin':
            return RemoteWindowsHelper(cluster_spec, test_config, os)
        else:
            return RemoteLinuxHelper(cluster_spec, test_config, os)

    @staticmethod
    def detect_os(cluster_spec):
        logger.info('Detecting OS')
        with settings(host_string=cluster_spec.yield_hostnames().next()):
            os = run('python -c "import platform; print platform.dist()[0]"',
                     pty=True)
        if os:
            return os
        else:
            return 'Cygwin'


class CurrentHostMutex:

    def __init__(self):
        with open("/tmp/current_host.txt", 'w') as f:
            f.write("0")

    @staticmethod
    def next_host_index():
        with open("/tmp/current_host.txt", 'r') as f:
            next_host = f.readline()
        with open("/tmp/current_host.txt", 'w') as f:
            f.write((int(next_host) + 1).__str__())
        return int(next_host)


class RemoteLinuxHelper(object):

    ARCH = {'i386': 'x86', 'x86_64': 'x86_64', 'unknown': 'x86_64'}

    CB_DIR = '/opt/couchbase'

    PROCESSES = ('beam.smp', 'memcached', 'epmd', 'cbq-engine', 'indexer',
                 'cbft', 'goport', 'goxdcr', 'couch_view_index_updater',
                 'moxi', 'spring')

    def __init__(self, cluster_spec, test_config, os):
        self.os = os
        self.hosts = tuple(cluster_spec.yield_hostnames())
        self.kv_hosts = tuple(cluster_spec.yield_kv_servers())
        self.current_host = CurrentHostMutex()
        self.cluster_spec = cluster_spec
        self.test_config = test_config

    @staticmethod
    def wget(url, outdir='/tmp', outfile=None):
        logger.info('Fetching {}'.format(url))
        if outfile is not None:
            run('wget -nc "{}" -P {} -O {}'.format(url, outdir, outfile))
        else:
            run('wget -N "{}" -P {}'.format(url, outdir))

    @single_host
    def detect_pkg(self):
        logger.info('Detecting package manager')
        if self.os.upper() in ('UBUNTU', 'DEBIAN'):
            return 'deb'
        else:
            return 'rpm'

    @single_host
    def detect_arch(self):
        logger.info('Detecting platform architecture')
        arch = run('uname -i', pty=True)
        return self.ARCH[arch]

    @single_host
    def detect_os_release(self):
        """Possible values:
            CentOS release 6.x (Final)
            CentOS Linux release 7.2.1511 (Core)
        """
        return run('cat /etc/redhat-release').split()[-2][0]

    def run_cbindex_command(self, command):
        command_path = '/opt/couchbase/bin/cbindex'
        command = "{path} {cmd}".format(path=command_path, cmd=command)
        logger.info('Submitting cbindex command {}'.format(command))

        status = run(command, shell_escape=False, pty=False)
        if status:
            logger.info('Command status {}'.format(status))

    def build_index(self, index_node, bucket_indexes):
        all_indexes = ",".join(bucket_indexes)
        cmd_str = "-auth=Administrator:password -server {index_node} -type build -indexes {all_indexes}" \
            .format(index_node=index_node, all_indexes=all_indexes)

        self.run_cbindex_command(cmd_str)

    def create_index(self, index_nodes, bucket, indexes, fields, secondarydb, where_map):
        # Remember what bucket:index was created
        bucket_indexes = []

        for index, field in zip(indexes, fields):
            cmd = "-auth=Administrator:password  -server {index_node}  -type create -bucket {bucket}" \
                  "  -fields={field}".format(index_node=index_nodes[0], bucket=bucket, field=field)

            if secondarydb:
                cmd = '{cmd} -using {db}'.format(cmd=cmd, db=secondarydb)

            if index in where_map and field in where_map[index]:
                # Partition indexes over index nodes by deploying index with
                # where clause on the corresponding index node
                where_list = where_map[index][field]
                for i, (index_node, where) in enumerate(
                        zip(index_nodes, where_list)):
                    index_i = index + "_{}".format(i)
                    # Since .format() is sensitive to {}, use % formatting
                    with_str_template = \
                        r'{\\\"defer_build\\\":true, \\\"nodes\\\":[\\\"%s\\\"]}'
                    with_str = with_str_template % index_node

                    final_cmd = "{cmd} -index {index} -where='{where}' -with=\\\"{with_str}\\\""\
                        .format(cmd=cmd, index=index_i, where=where, with_str=with_str)

                    bucket_indexes.append("{}:{}".format(bucket, index_i))
                    self.run_cbindex_command(final_cmd)
            else:
                # no partitions, no where clause
                with_str = r'{\\\"defer_build\\\":true}'
                final_cmd = "{cmd} -index {index} -with=\\\"{with_str}\\\""\
                    .format(cmd=cmd, index=index, with_str=with_str)

                bucket_indexes.append("{}:{}".format(bucket, index))
                self.run_cbindex_command(final_cmd)
        return bucket_indexes

    @single_host
    def build_secondary_index(self, index_nodes, bucket, indexes, fields,
                              secondarydb, where_map):
        logger.info('building secondary indexes')

        # Create index but do not build
        bucket_indexes = self.create_index(index_nodes, bucket, indexes, fields, secondarydb, where_map)
        time.sleep(10)

        # build indexes
        self.build_index(index_nodes[0], bucket_indexes)

    @all_kv_nodes
    def run_spring_on_kv(self, ls, silent=False):

        def calculate_existing_items():
            if ls.creates == 100:
                return operation * host + ls.existing_items
            return ls.existing_items

        throughput = int(ls.throughput) if ls.throughput != float('inf') \
            else ls.throughput
        items_in_working_set = int(ls.working_set)
        operations_to_hit_working_set = ls.working_set_access
        logger.info("running spring on kv nodes")
        number_of_kv_nodes = self.kv_hosts.__len__()
        existing_item = operation = 0
        sleep_time = uniform(1, number_of_kv_nodes)
        time.sleep(sleep_time)
        host = self.current_host.next_host_index()
        logger.info("current_host_index {}".format(host))
        if host != number_of_kv_nodes - 1:
            if (ls.creates != 0 or ls.reads != 0 or ls.updates != 0 or ls.deletes != 0) \
                    and ls.items != float('inf'):
                operation = int(ls.items / (number_of_kv_nodes * 100)) * 100
                existing_item = calculate_existing_items()
        else:
            if (ls.creates != 0 or ls.reads != 0 or ls.updates != 0 or ls.deletes != 0) \
                    and ls.items != float('inf'):
                operation = \
                    ls.items - (int(ls.items / (number_of_kv_nodes * 100)) * 100 * (number_of_kv_nodes - 1))
                existing_item = calculate_existing_items()
                self.current_host = CurrentHostMutex()
        time.sleep(number_of_kv_nodes * 2 - sleep_time)
        cmdstr = "spring -c {} -r {} -u {} -d {} -e {} -s {} -i {} -w {} -W {} -n {}"\
            .format(ls.creates, ls.reads, ls.updates, ls.deletes, ls.expiration, ls.size, existing_item,
                    items_in_working_set, operations_to_hit_working_set, ls.spring_workers, self.hosts[0])
        if operation != 0:
            cmdstr += " -o {}".format(operation)
        if throughput != float('inf'):
            thrput = int(throughput / (number_of_kv_nodes * 100)) * 100
            cmdstr += " -t {}".format(thrput)
        cmdstr += " cb://Administrator:password@{}:8091/bucket-1".format(self.hosts[0])
        pty = True
        if silent:
            cmdstr += " >/tmp/springlog.log 2>&1 &"
            pty = False
        logger.info(cmdstr)
        result = run(cmdstr, pty=pty)
        if silent:
            time.sleep(10)
            res = run(r"ps aux | grep -ie spring | awk '{print $11}'")
            if "python" not in res:
                raise Exception("Spring not run on KV. {}".format(res))
        else:
            if "Current progress: 100.00 %" not in result and "Finished: worker-{}".format(ls.spring_workers - 1) not in result:
                raise Exception("Spring not run on KV")

    @all_kv_nodes
    def check_spring_running(self):
        cmdstr = r"ps aux | grep -ie spring | awk '{print $11}'"
        result = run(cmdstr)
        logger.info(result)
        logger.info(result.stdout)

    @all_hosts
    def reset_swap(self):
        logger.info('Resetting swap')
        run('swapoff --all && swapon --all')

    @all_hosts
    def drop_caches(self):
        logger.info('Dropping memory cache')
        run('sync && echo 3 > /proc/sys/vm/drop_caches')

    @all_hosts
    def set_swappiness(self):
        logger.info('Changing swappiness to 0')
        run('sysctl vm.swappiness=0')

    @all_hosts
    def disable_thp(self):
        for path in (
            '/sys/kernel/mm/transparent_hugepage/enabled',
            '/sys/kernel/mm/redhat_transparent_hugepage/enabled',
        ):
            run('echo never > {}'.format(path), quiet=True)

    @all_hosts
    def collect_info(self):
        logger.info('Running cbcollect_info')

        run('rm -f /tmp/*.zip')

        fname = '/tmp/{}.zip'.format(uhex())
        try:
            r = run('{}/bin/cbcollect_info {}'.format(self.CB_DIR, fname),
                    warn_only=True, timeout=1200)
        except CommandTimeout:
            logger.error('cbcollect_info timed out')
            return
        if not r.return_code:
            get('{}'.format(fname))
            run('rm -f {}'.format(fname))

    @all_hosts
    def clean_data(self):
        for path in self.cluster_spec.paths:
            run('rm -fr {}/*'.format(path))
        run('rm -fr {}'.format(self.CB_DIR))

    @all_hosts
    def kill_processes(self):
        logger.info('Killing {}'.format(', '.join(self.PROCESSES)))
        run('killall -9 {}'.format(' '.join(self.PROCESSES)),
            warn_only=True, quiet=True)

    @all_hosts
    def uninstall_couchbase(self, pkg):
        logger.info('Uninstalling Couchbase Server')
        if pkg == 'deb':
            run('yes | apt-get remove couchbase-server', quiet=True)
            run('yes | apt-get remove couchbase-server-community', quiet=True)
        else:
            run('yes | yum remove couchbase-server', quiet=True)
            run('yes | yum remove couchbase-server-community', quiet=True)

    @all_hosts
    def install_couchbase(self, pkg, url, filename, version=None):
        self.wget(url, outdir='/tmp')

        logger.info('Installing Couchbase Server')
        if pkg == 'deb':
            run('yes | apt-get install gdebi')
            run('yes | gdebi /tmp/{}'.format(filename))
        else:
            run('yes | rpm -i /tmp/{}'.format(filename))

    @all_hosts
    def restart(self):
        logger.info('Restarting server')
        run('service couchbase-server restart', pty=False)

    @all_hosts
    def restart_with_alternative_num_vbuckets(self, num_vbuckets):
        logger.info('Changing number of vbuckets to {}'.format(num_vbuckets))
        run('systemctl set-environment COUCHBASE_NUM_VBUCKETS={}'
            .format(num_vbuckets))
        run('service couchbase-server restart', pty=False)
        run('systemctl unset-environment COUCHBASE_NUM_VBUCKETS')

    @all_hosts
    def stop_server(self):
        logger.info('Stopping Couchbase Server')
        run('/etc/init.d/couchbase-server stop', pty=False)

    @all_hosts
    def start_server(self):
        logger.info('Starting Couchbase Server')
        run('/etc/init.d/couchbase-server start', pty=False)

    def detect_if(self):
        stdout = run("ip route list | grep default")
        return stdout.strip().split()[4]

    def detect_ip(self, _if):
        stdout = run('ifdata -pa {}'.format(_if))
        return stdout.strip()

    @all_hosts
    def disable_wan(self):
        logger.info('Disabling WAN effects')
        _if = self.detect_if()
        run('tc qdisc del dev {} root'.format(_if), warn_only=True, quiet=True)

    @all_hosts
    def enable_wan(self):
        logger.info('Enabling WAN effects')
        _if = self.detect_if()
        for cmd in (
            'tc qdisc add dev {} handle 1: root htb',
            'tc class add dev {} parent 1: classid 1:1 htb rate 1gbit',
            'tc class add dev {} parent 1:1 classid 1:11 htb rate 1gbit',
            'tc qdisc add dev {} parent 1:11 handle 10: netem delay 40ms 2ms '
            'loss 0.005% 50% duplicate 0.005% corrupt 0.005%',
        ):
            run(cmd.format(_if))

    @all_hosts
    def filter_wan(self, src_list, dest_list):
        logger.info('Filtering WAN effects')
        _if = self.detect_if()

        if self.detect_ip(_if) in src_list:
            _filter = dest_list
        else:
            _filter = src_list

        for ip in _filter:
            run('tc filter add dev {} protocol ip prio 1 u32 '
                'match ip dst {} flowid 1:11'.format(_if, ip))

    @all_hosts
    def detect_core_dumps(self):
        # Based on kernel.core_pattern = /tmp/core.%e.%p.%h.%t
        r = run('ls /tmp/core*', quiet=True)
        if not r.return_code:
            return r.split()
        else:
            return []

    @all_hosts
    def tune_log_rotation(self):
        logger.info('Tune log rotation so that it happens less frequently')
        run('sed -i "s/num_files, [0-9]*/num_files, 50/" '
            '/opt/couchbase/etc/couchbase/static_config')

    @single_host
    def cbrestorefts(self, archive_path, repo_path):
        '''
        This is updated cbrestore for spock support. As we move to spock, old version is not needed
        ft-indexes is disabled to not to take any backup for indices.
        '''
        cmd = "cd /opt/couchbase/bin && ./cbbackupmgr restore --include-buckets={}  --archive {} --repo {} " \
              " --host http://localhost:8091 --username Administrator " \
              "--password password --threads 30 --disable-ft-indexes --disable-gsi-indexes".\
            format(self.test_config.buckets[0], archive_path, repo_path)
        run(cmd)

    @single_host
    def startelasticsearchplugin(self):
        cmd = ['service elasticsearch restart',
               'service elasticsearch stop',
               'sudo chkconfig --add elasticsearch',
               'sudo service elasticsearch restart',
               'sudo service elasticsearch stop',
               'cd /usr/share/elasticsearch',
               'echo "couchbase.password: password" >> /etc/elasticsearch/elasticsearch.yml',
               '/usr/share/elasticsearch/bin/plugin  -install transport-couchbase -url http://packages.couchbase.com.s3.amazonaws.com/releases/elastic-search-adapter" \
                                                      "/2.0.0/elasticsearch-transport-couchbase-2.0.0.zip',
               'echo "couchbase.password: password" >> /etc/elasticsearch/elasticsearch.yml',
               'echo "couchbase.username: Administrator" >> /etc/elasticsearch/elasticsearch.yml'
               'bin/plugin -install mobz/elasticsearch-head',
               'sudo service elasticsearch restart']

        for c in cmd.split:
            cmd = c.strip()
            logger.info("command executed {}".format(cmd))
            run(cmd)

    @all_hosts
    def start_bandwidth_monitor(self, track_time=1):
        """Run iptraf to generate various network statistics and sent output to log file
        track_time tells IPTraf to run the specified facility for only timeout minutes.
        """
        kill_command = "pkill -9 iptraf; rm -rf /tmp/iptraf.log; rm -rf /var/lock/iptraf/*; " \
                       "rm -rf /var/log/iptraf/*"
        start_command = "sudo iptraf -i eth0 -L /tmp/iptraf.log -t %d -B /dev/null" % track_time
        for i in range(2):
            run(kill_command)
            run(start_command)
            time.sleep(2)
            res = run('ps -ef| grep "iptraf" | grep tmp')
            logger.info('print res', res)
            if not res:
                time.sleep(2)
            else:
                break

    @all_hosts
    def read_bandwidth_stats(self, type, servers):
        result = 0
        for server in servers:
            server_ip = server.split(':')[0]
            command = "cat /tmp/iptraf.log | grep 'FIN sent' | grep '" + type + " " + server_ip + ":11210'"
            logger.info(run(command, quiet=True))
            command += "| awk 'BEGIN{FS = \";\"}{if ($0 ~ /packets/) {if ($6 ~ /FIN sent/)" \
                       " {print $7}}}' | awk '{print $3}'"
            temp = run(command, quiet=True)
            if temp:
                arr = [int(t) for t in temp.split("\r\n")]
                result += int(sum(arr))
            time.sleep(3)
        return result

    @all_hosts
    def kill_process(self, process=''):
        command = "pkill -9 %s" % process
        run(command, quiet=True)

    @single_host
    def install_beer_samples(self):
        logger.info('run install_beer_samples')
        cmd = '/opt/couchbase/bin/cbdocloader  -n localhost:8091 -u Administrator -p password -b beer-sample /opt/couchbase/samples/beer-sample.zip'
        result = run(cmd, pty=False)
        return result

    @single_client
    def ycsb_load_run(self, path, cmd, log_path=None, mypid=0):
        state.connections.clear()
        if log_path:
            tmpcmd = 'rm -rf ' + log_path
            run(tmpcmd)
            tmpcmd = 'mkdir -p ' + log_path
            run(tmpcmd)
        load_run_cmd = 'cd {}'.format(path) + '_' + str(mypid) + ' && mvn -pl com.yahoo.ycsb:couchbase2-binding -am ' \
                                              'clean package -Dmaven.test.skip -Dcheckstyle.skip=true && {}'.format(cmd)
        logger.info(" running command {}".format(load_run_cmd))
        return run(load_run_cmd)

    @all_hosts
    def fio(self, config):
        logger.info('Running fio job: {}'.format(config))
        filename = os.path.basename(config)
        remote_path = os.path.join('/tmp', filename)
        put(config, remote_path)
        return run('fio --minimal {}'.format(remote_path))


class RemoteWindowsHelper(RemoteLinuxHelper):

    CB_DIR = '/cygdrive/c/Program\ Files/Couchbase/Server'

    VERSION_FILE = '/cygdrive/c/Program Files/Couchbase/Server/VERSION.txt'

    MAX_RETRIES = 5

    TIMEOUT = 600

    SLEEP_TIME = 60  # crutch

    PROCESSES = ('erl*', 'epmd*')

    @staticmethod
    def exists(fname):
        r = run('test -f "{}"'.format(fname), warn_only=True, quiet=True)
        return not r.return_code

    @single_host
    def detect_pkg(self):
        return 'exe'

    def reset_swap(self):
        pass

    def drop_caches(self):
        pass

    def set_swappiness(self):
        pass

    def disable_thp(self):
        pass

    def detect_ip(self):
        return run('ipconfig | findstr IPv4').split(': ')[1]

    @all_hosts
    def collect_info(self):
        logger.info('Running cbcollect_info')

        run('rm -f *.zip')

        fname = '{}.zip'.format(uhex())
        r = run('{}/bin/cbcollect_info.exe {}'.format(self.CB_DIR, fname),
                warn_only=True)
        if not r.return_code:
            get('{}'.format(fname))
            run('rm -f {}'.format(fname))

    @all_hosts
    def clean_data(self):
        for path in self.cluster_spec.paths:
            path = path.replace(':', '').replace('\\', '/')
            path = '/cygdrive/{}'.format(path)
            run('rm -fr {}/*'.format(path))

    @all_hosts
    def kill_processes(self):
        logger.info('Killing {}'.format(', '.join(self.PROCESSES)))
        run('taskkill /F /T /IM {}'.format(' /IM '.join(self.PROCESSES)),
            warn_only=True, quiet=True)

    def kill_installer(self):
        run('taskkill /F /T /IM setup.exe', warn_only=True, quiet=True)

    def clean_installation(self):
        with settings(warn_only=True):
            run('rm -fr {}'.format(self.CB_DIR))

    @all_hosts
    def uninstall_couchbase(self, pkg):
        local_ip = self.detect_ip()
        logger.info('Uninstalling Package on {}'.format(local_ip))

        if self.exists(self.VERSION_FILE):
            for retry in range(self.MAX_RETRIES):
                self.kill_installer()
                try:
                    r = run('./setup.exe -s -f1"C:\\uninstall.iss"',
                            warn_only=True, quiet=True, timeout=self.TIMEOUT)
                    if not r.return_code:
                        t0 = time.time()
                        while self.exists(self.VERSION_FILE) and \
                                time.time() - t0 < self.TIMEOUT:
                            logger.info('Waiting for Uninstaller to finish on {}'.format(local_ip))
                            time.sleep(5)
                        break
                    else:
                        logger.warn('Uninstall script failed to run on {}'.format(local_ip))
                except CommandTimeout:
                    logger.warn("Uninstall command timed out - retrying on {} ({} of {})"
                                .format(local_ip, retry, self.MAX_RETRIES))
                    continue
            else:
                logger.warn('Uninstaller failed with no more retries on {}'
                            .format(local_ip))
        else:
            logger.info('Package not present on {}'.format(local_ip))

        logger.info('Cleaning registry on {}'.format(local_ip))
        self.clean_installation()

    @staticmethod
    def put_iss_files(version):
        logger.info('Copying {} ISS files'.format(version))
        put('iss/install_{}.iss'.format(version),
            '/cygdrive/c/install.iss')
        put('iss/uninstall_{}.iss'.format(version),
            '/cygdrive/c/uninstall.iss')

    @all_hosts
    def install_couchbase(self, pkg, url, filename, version=None):
        self.kill_installer()
        run('rm -fr setup.exe')
        self.wget(url, outfile='setup.exe')
        run('chmod +x setup.exe')

        self.put_iss_files(version)

        local_ip = self.detect_ip()

        logger.info('Installing Package on {}'.format(local_ip))
        try:
            run('./setup.exe -s -f1"C:\\install.iss"')
        except:
            logger.error('Install script failed on {}'.format(local_ip))
            raise

        while not self.exists(self.VERSION_FILE):
            logger.info('Waiting for Installer to finish on {}'.format(local_ip))
            time.sleep(5)

        logger.info('Sleeping for {} seconds'.format(self.SLEEP_TIME))
        time.sleep(self.SLEEP_TIME)

    def restart(self):
        pass

    def restart_with_alternative_num_vbuckets(self, num_vbuckets):
        pass

    def disable_wan(self):
        pass

    def enable_wan(self):
        pass

    def filter_wan(self, *args):
        pass

    def tune_log_rotation(self):
        pass

    @all_hosts
    def stop_server(self):
        logger.info('Stopping Couchbase Server')
        run('net stop CouchbaseServer')

    @all_hosts
    def start_server(self):
        logger.info('Starting Couchbase Server')
        run('net start CouchbaseServer')
