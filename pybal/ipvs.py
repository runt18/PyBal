"""
ipvsadm.py
Copyright (C) 2006-2015 by Mark Bergsma <mark@nedworks.org>

LVS state/configuration classes for PyBal
"""
from . import util

import os
log = util.log


class IPVSManager(object):
    """Class that provides a mapping from abstract LVS commands / state
    changes to ipvsadm command invocations."""

    ipvsPath = '/sbin/ipvsadm'

    DryRun = True

    Debug = False

    @classmethod
    def modifyState(cls, cmdList):
        """
        Changes the state using a supplied list of commands (by invoking ipvsadm)
        """

        if cls.Debug:
            print cmdList
        if cls.DryRun: return

        command = [cls.ipvsPath, '-R']
        stdin = os.popen(" ".join(command), 'w')
        for line in cmdList:
            stdin.write(line + '\n')
        stdin.close()

        # FIXME: Check return code and act on failure



    @staticmethod
    def subCommandService(service):
        """Returns a partial command / parameter list as a single
        string, that describes the supplied LVS service, ready for
        passing to ipvsadm.

        Arguments:
            service:    tuple(protocol, address, port, ...)
        """

        protocol = {'tcp': '-t',
                    'udp': '-u'}[service[0]]

        if ':' in service[1]:
            # IPv6 address
            service = ' [{0!s}]:{1:d}'.format(*service[1:3])
        else:
            # IPv4
            service = ' {0!s}:{1:d}'.format(*service[1:3])

        return protocol + service

    @staticmethod
    def subCommandServer(server):
        """Returns a partial command / parameter list as a single
        string, that describes the supplied server, ready for passing
        to ipvsadm.

        Arguments:
            server:    PyBal server object
        """

        return '-r {0!s}'.format((server.ip or server.host))

    @staticmethod
    def commandClearServiceTable():
        """Returns an ipvsadm command to clear the current service
        table."""
        return '-C'

    @classmethod
    def commandRemoveService(cls, service):
        """Returns an ipvsadm command to remove a single service."""
        return '-D ' + cls.subCommandService(service)

    @classmethod
    def commandAddService(cls, service):
        """Returns an ipvsadm command to add a specified service.

        Arguments:
            service:    tuple(protocol, address, port, ...)
        """

        cmd = '-A ' + cls.subCommandService(service)

        # Include scheduler if specified
        if len(service) > 3:
            cmd += ' -s ' + service[3]

        return cmd

    @classmethod
    def commandRemoveServer(cls, service, server):
        """Returns an ipvsadm command to remove a server from a service.

        Arguments:
            service:   tuple(protocol, address, port, ...)
            server:    Server
        """

        return " ".join(['-d', cls.subCommandService(service),
                         cls.subCommandServer(server)])

    @classmethod
    def commandAddServer(cls, service, server):
        """Returns an ipvsadm command to add a server to a service.

        Arguments:
            service:   tuple(protocol, address, port, ...)
            server:    Server
        """

        cmd = " ".join(['-a', cls.subCommandService(service),
                        cls.subCommandServer(server)])

        # Include weight if specified
        if server.weight:
            cmd += ' -w {0:d}'.format(server.weight)

        return cmd

    @classmethod
    def commandEditServer(cls, service, server):
        """Returns an ipvsadm command to edit the parameters of a
        server.

        Arguments:
            service:   tuple(protocol, address, port, ...)
            server:    Server
        """

        cmd = " ".join(['-e', cls.subCommandService(service),
                        cls.subCommandServer(server)])

        # Include weight if specified
        if server.weight:
            cmd += ' -w {0:d}'.format(server.weight)

        return cmd


class LVSService:
    """Class that maintains the state of a single LVS service
    instance."""

    ipvsManager = IPVSManager

    SVC_PROTOS = ('tcp', 'udp')
    SVC_SCHEDULERS = ('rr', 'wrr', 'lc', 'wlc', 'lblc', 'lblcr', 'dh', 'sh',
                      'sed', 'nq')

    def __init__(self, name, (protocol, ip, port, scheduler), configuration):
        """Constructor"""

        self.name = name
        self.servers = set()

        if (protocol not in self.SVC_PROTOS or
                scheduler not in self.SVC_SCHEDULERS):
            raise ValueError('Invalid protocol or scheduler')

        self.protocol = protocol
        self.ip = ip
        self.port = port
        self.scheduler = scheduler

        self.configuration = configuration

        self.ipvsManager.DryRun = configuration.getboolean('dryrun', False)
        self.ipvsManager.Debug = configuration.getboolean('debug', False)

        if self.configuration.getboolean('bgp', False):
            from pybal import BGPFailover
            # Add service ip to the BGP announcements
            BGPFailover.addPrefix(self.ip)

        self.createService()

    def service(self):
        """Returns a tuple (protocol, ip, port, scheduler) that
        describes this LVS instance."""

        return (self.protocol, self.ip, self.port, self.scheduler)

    def createService(self):
        """Initializes this LVS instance in LVS."""

        # Remove a previous service and add the new one
        cmdList = [self.ipvsManager.commandRemoveService(self.service()),
                   self.ipvsManager.commandAddService(self.service())]
        self.ipvsManager.modifyState(cmdList)

    def assignServers(self, newServers):
        """Takes a (new) set of servers (as a host->Server dictionary)
        and updates the LVS state accordingly."""

        cmdList = (
            [self.ipvsManager.commandAddServer(self.service(), server)
             for server in newServers - self.servers] +
            [self.ipvsManager.commandEditServer(self.service(), server)
             for server in newServers & self.servers] +
            [self.ipvsManager.commandRemoveServer(self.service(), server)
             for server in self.servers - newServers]
        )

        self.servers = newServers
        self.ipvsManager.modifyState(cmdList)

    def addServer(self, server):
        """Adds (pools) a single Server to the LVS state."""

        if server not in self.servers:
            cmdList = [self.ipvsManager.commandAddServer(self.service(),
                                                         server)]
        else:
            log.warn('bug: adding already existing server to LVS')
            cmdList = [self.ipvsManager.commandEditServer(self.service(),
                                                          server)]

        self.servers.add(server)

        self.ipvsManager.modifyState(cmdList)
        server.pooled = True

    def removeServer(self, server):
        """Removes (depools) a single Server from the LVS state."""

        cmdList = [self.ipvsManager.commandRemoveServer(self.service(),
                                                        server)]

        self.servers.remove(server)  # May raise KeyError

        server.pooled = False
        self.ipvsManager.modifyState(cmdList)

    def initServer(self, server):
        """Initializes a server instance with LVS service specific
        configuration."""

        server.port = self.port

    def getDepoolThreshold(self):
        """Returns the threshold below which no more down servers will
        be depooled."""

        return self.configuration.getfloat('depool-threshold', .5)
