"""
  Instrumentation HTTP server for PyBal
  ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

  A simple http server that can return the state of a pool,
  or the state of a single server within a pool.

  Urls:

  /pools  - a list of the available pools
  /pools/<pool> - The full state of a pool
  /pools/<pool>/<host> - the state of a single host in a pool

  All results are returned either as human-readable lists or as json
  structures, depending on the Accept header of the request.
"""

from twisted.web.resource import Resource
import json


def wantJson(request):
    return (request.requestHeaders.hasHeader('Accept')
            and 'application/json' in
            request.requestHeaders.getRawHeaders('Accept'))


class Resp404(Resource):
    isLeaf = True

    def render_GET(self, request):
        request.setResponseCode(404)
        msg = {'error':
               "The desired url {0} was not found".format(request.uri)}
        if wantJson(request):
            return json.dumps(msg)
        else:
            return msg['error']


class ServerRoot(Resource):
    """Root url resource"""

    def getChild(self, path, request):
        if path == 'pools':
            return PoolsRoot()
        if path == 'alerts':
            return Alerts()
        else:
            return Resp404()


class Alerts(Resource):
    alerting_services = {}
    isLeaf = True

    def render_GET(self, request):
        critPools = {}
        resp = {'status': 'ok', 'msg': ''}

        for pool, crd in PoolsRoot._pools.items():
            pooledDown = len(crd.pooledDownServers)
            total = len(crd.servers)
            if pooledDown:
                slist = ", ".join([s.host for s in crd.pooledDownServers])
                critPools[pool] = "Servers {0!s} are marked down but pooled".format(slist)
            elif total < (total * crd.lvsservice.getDepoolThreshold() + 1):
                resp['status'] = 'warning'
                resp['msg'] += "Pool {0!s} is too small to allow depooling. ".format(crd.lvsservice.name)
        if critPools != {}:
            resp['status'] = 'critical'
            resp['msg'] = "; ".join(["{0!s}: {1!s}".format(k, v)
                                    for k, v in critPools.items()])

        if resp['status'] == 'ok':
            resp['msg'] = 'All pools are healthy'
        if wantJson(request):
            return json.dumps(resp)
        else:
            return "{0!s} - {1!s}".format(resp['status'].upper(), resp['msg'])

class PoolsRoot(Resource):
    """Pools base resource.

    Serves /pools

    If called directly, will list all the pools.
    """
    _pools = {}

    @classmethod
    def addPool(cls, path, coordinator):
        cls._pools[path] = coordinator

    def getChild(self, path, request):
        if not path:
            return self
        if path in self._pools:
            return PoolServers(self._pools[path])
        return Resp404()

    def render_GET(self, request):
        pools = self._pools.keys()
        if wantJson(request):
            return json.dumps(pools)
        else:
            return "\n".join(pools) + "\n"


class PoolServers(Resource):
    """Single pool resource.

    Serves /pools/<pool>

    It will print out the state of all the servers in the pool, one per line.
    """
    def __init__(self, coordinator):
        Resource.__init__(self)
        self.coordinator = coordinator

    def getChild(self, path, request):
        if not path:
            return self
        if path in self.coordinator.servers:
            return PoolServer(self.coordinator.servers[path])
        return Resp404()

    def render_GET(self, request):
        if wantJson(request):
            res = {}
            for hostname, server in self.coordinator.servers.items():
                res[hostname] = server.dumpState()
            return json.dumps(res)
        else:
            res = ""
            for hostname, server in self.coordinator.servers.items():
                res += "{0}:\t{1}\n".format(hostname, server.textStatus())
            return res


class PoolServer(Resource):
    """
    Single server resource.

    Serves /pools/<pool>/<hostname>
    """
    isLeaf = True

    def __init__(self, server):
        self.server = server

    def render_GET(self, request):
        if wantJson(request):
            return json.dumps(self.server.dumpState())
        else:
            return self.server.textStatus() + "\n"
