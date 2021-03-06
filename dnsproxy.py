#!/usr/bin/env python
# coding:utf-8

import sys
import os

try:
    import gevent
    import gevent.queue
    import gevent.monkey
    import gevent.coros
    import gevent.server
    import gevent.pool
    gevent.monkey.patch_all(dns=gevent.version_info[0]>=1)
except ImportError:
    if os.name == 'nt':
        sys.stderr.write('WARNING: python-gevent not installed. `http://code.google.com/p/gevent/downloads/list`\n')
    else:
        sys.stderr.write('WARNING: python-gevent not installed. `curl -k -L http://git.io/I9B7RQ|sh`\n')
    import Queue
    import thread
    import threading
    import SocketServer

    def GeventImport(name):
        import sys
        sys.modules[name] = type(sys)(name)
        return sys.modules[name]
    def GeventSpawn(target, *args, **kwargs):
        return thread.start_new_thread(target, args, kwargs)
    def GeventSpawnLater(seconds, target, *args, **kwargs):
        def wrap(*args, **kwargs):
            import time
            time.sleep(seconds)
            return target(*args, **kwargs)
        return thread.start_new_thread(wrap, args, kwargs)
    class GeventServerStreamServer(SocketServer.ThreadingTCPServer):
        allow_reuse_address = True
        def __init__(self, server_address, *args, **kwargs):
            SocketServer.ThreadingTCPServer.__init__(self, server_address, *args, **kwargs)
            self.address = self.server_address
        def finish_request(self, request, client_address):
            self.RequestHandlerClass(request, client_address)
    class GeventServerDatagramServer(SocketServer.ThreadingUDPServer):
        allow_reuse_address = True
        def __init__(self, server_address, *args, **kwargs):
            SocketServer.ThreadingUDPServer.__init__(self, server_address, self.RequestHandlerClass, *args, **kwargs)
            self.address = self.server_address
            self._writelock = threading.Semaphore()
        def sendto(self, *args):
            self._writelock.acquire()
            try:
                self.socket.sendto(*args)
            finally:
                self._writelock.release()
        def RequestHandlerClass(self, (data, server_socket), client_addr, server):
            return self.handle(data, client_addr)
        def handle(self, data, address):
            raise NotImplemented()
    class GeventPoolPool(object):
        def __init__(self, size):
            self._lock = threading.Semaphore(size)
        def __target_wrapper(self, target, args, kwargs):
            t = threading.Thread(target=target, args=args, kwargs=kwargs)
            try:
                t.start()
                t.join()
            except Exception as e:
                logging.error('threading.Thread target=%r error:%s', target, e)
            finally:
                self._lock.release()
        def spawn(self, target, *args, **kwargs):
            self._lock.acquire()
            return thread.start_new_thread(self.__target_wrapper, (target, args, kwargs))

    gevent        = GeventImport('gevent')
    gevent.queue  = GeventImport('gevent.queue')
    gevent.coros  = GeventImport('gevent.coros')
    gevent.server = GeventImport('gevent.server')
    gevent.pool   = GeventImport('gevent.pool')

    gevent.queue.Queue           = Queue.Queue
    gevent.coros.Semaphore       = threading.Semaphore
    gevent.getcurrent            = threading.currentThread
    gevent.spawn                 = GeventSpawn
    gevent.spawn_later           = GeventSpawnLater
    gevent.server.StreamServer   = GeventServerStreamServer
    gevent.server.DatagramServer = GeventServerDatagramServer
    gevent.pool.Pool             = GeventPoolPool

    del GeventImport, GeventSpawn, GeventSpawnLater, GeventServerStreamServer, GeventServerDatagramServer, GeventPoolPool

import re
import time
import SocketServer
import threading
import logging
import socket
import struct
import collections

class DNSServer(gevent.server.DatagramServer):
    """DNS Proxy over TCP to avoid DNS poisoning"""
    remote_address = ('8.8.8.8', 53)
    max_wait = 2
    max_retry = 2
    max_cache_size = 2000
    timeout   = 3
    dns_blacklist = set(['203.98.7.65','159.106.121.75','159.24.3.173','46.82.174.68','78.16.49.15','59.24.3.173','243.185.187.39','243.185.187.30','8.7.198.45','37.61.54.158','93.46.8.89',])

    def __init__(self, *args, **kwargs):
        gevent.server.DatagramServer.__init__(self, *args, **kwargs)
        self.cache = {}
    def handle(self, data, address):
        cache   = self.cache
        timeout = self.timeout
        remote_address = self.remote_address
        reqid   = data[:2]
        domain  = data[12:data.find('\x00', 12)]
        if len(cache) > self.max_cache_size:
            cache.clear()
        if domain in cache:
            return self.sendto(reqid + cache[domain][2:], address)
        retry = 0
        while domain not in cache:
            qname = re.sub(r'[\x01-\x10]', '.', domain[1:])
            logging.info('DNSServer resolve domain=%r to iplist', qname)
            sock = None
            try:
                data = '%s\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00%s\x00\x00\x01\x00\x01' % (os.urandom(2), domain)
                address_family = socket.AF_INET6 if ':' in remote_address[0] else socket.AF_INET
                sock = socket.socket(family=address_family, type=socket.SOCK_DGRAM)
                if isinstance(timeout, (int, long)):
                    sock.settimeout(timeout)
                sock.sendto(data, remote_address)
                for i in xrange(self.max_wait):
                    print (i, )
                    data, address = sock.recvfrom(512)
                    iplist = ['.'.join(str(ord(x)) for x in s) for s in re.findall('\x00\x01\x00\x01.{6}(.{4})', data)]
                    if not any(x in self.dns_blacklist for x in iplist):
                        if not iplist:
                            logging.info('DNS return unkown result, iplist=%s', iplist)
                        cache[domain] = data
                        self.sendto(reqid + cache[domain][2:], address)
                        break
                    else:
                        logging.info('DNS Poisoning return %s from %s', iplist, sock)
            except socket.error as e:
                logging.error('DNSServer resolve domain=%r to iplist failed:%s', qname, e)
            finally:
                if sock:
                    sock.close()
                retry += 1
                if retry >= self.max_retry:
                    break

def main():
    logging.basicConfig(level=logging.DEBUG, format='%(levelname)s - %(asctime)s %(message)s', datefmt='[%b %d %H:%M:%S]')
    server = DNSServer(('', 53))
    logging.info('serving at %r', server.address)
    server.serve_forever()

if __name__ == '__main__':
    main()
