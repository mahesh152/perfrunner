import socket

from decorator import decorator
from logger import logger
from seriesly import Seriesly
from seriesly.exceptions import BadRequest, ConnectionError


def _memoize(method, self, db):
    if db not in method.cache:
        method.cache[db] = method(self, db)
    return method.cache[db]


def memoize(method):
    method.cache = {}
    return decorator(_memoize, method)


class SerieslyStore(object):

    def __init__(self, host):
        self.seriesly = Seriesly(host)

    @staticmethod
    def build_dbname(cluster, server, bucket, index, collector):
        db_name = (collector or "") + cluster + (bucket or "") + (index or "") + (server or "")
        for char in "[]/\;.,><&*:%=+@!#^()|?^'\"":
            db_name = db_name.replace(char, "")
        return db_name

    @memoize
    def _get_db(self, db_name):
        try:
            existing_dbs = self.seriesly.list_dbs()
        except ConnectionError as e:
            logger.interrupt("seriesly not available: {}".format(e))
        else:
            if db_name not in existing_dbs:
                logger.info("Creating a new database: {}".format(db_name))
                self.seriesly.create_db(db_name)
            return self.seriesly[db_name]

    def append(self, data, cluster=None, server=None, bucket=None, index=None,
               collector=None, timestamp=None):
        db_name = self.build_dbname(cluster, server, bucket, index, collector)
        db = self._get_db(db_name)
        try:
            db.append(data, timestamp=timestamp)
        except (BadRequest, socket.error):  # Ignore bad requests
            pass

    def drop_db(self, cluster=None, server=None, bucket=None, index=None, collector=None):
        db_name = self.build_dbname(cluster, server, bucket, index, collector)
        try:
            existing_dbs = self.seriesly.list_dbs()
        except ConnectionError as e:
            logger.interrupt("seriesly not available: {}".format(e))
        else:
            if db_name not in existing_dbs:
                logger.info("DB not present: {}".format(db_name))
                return
            logger.info("Dropping DB: {}".format(db_name))
            self.seriesly.drop_db(db_name)
            return
