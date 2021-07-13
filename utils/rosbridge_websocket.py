from __future__ import print_function
import rospy
import sys
import os
import signal
import threading

from twisted.python import log
from twisted.internet import reactor, ssl
from twisted.internet.error import CannotListenError, ReactorNotRunning
from distutils.version import LooseVersion
import autobahn #to check version
from autobahn.twisted.websocket import WebSocketServerFactory, WebSocketClientFactory,listenWS
from autobahn.websocket.compress import (PerMessageDeflateOffer,
                                         PerMessageDeflateOfferAccept)
log.startLogging(sys.stdout)

from rosbridge_server import ClientManager
#from rosbridge_server.autobahn_websocket import RosbridgeWebSocket
from websocket_protocol import *
from rosbridge_server.util import get_ephemeral_port

from rosbridge_library.capabilities.advertise import Advertise
from rosbridge_library.capabilities.publish import Publish
from rosbridge_library.capabilities.subscribe import Subscribe
from rosbridge_library.capabilities.advertise_service import AdvertiseService
from rosbridge_library.capabilities.unadvertise_service import UnadvertiseService
from rosbridge_library.capabilities.call_service import CallService

class MyWebSocketClientFactory(WebSocketClientFactory):
    def __init__(self,parent=None):
        super(WebSocketClientFactory,self).__init__(parent)

    def doStop(self):
        if self.numPorts == 0:
            return
        self.numPorts = self.numPorts - 1
        if not self.numPorts:
            os.kill(os.getpid(), signal.SIGTERM)
            self.stopFactory()

class Websocket():
    def __init__(self,shared_data,is_server=True,parent=None):
        self.is_server=is_server

        self.retry_startup_delay = 2.0  # seconds
        self.use_compression = False
        self.bson_only_mode = False

        self.ping_interval = 0.
        self.ping_timeout = 0.
        self.null_origin = True

        # SSL options
        self.certfile = None
        self.keyfile = None
        self.external_port = None

        RosbridgeWebSocketServer.client_manager = ClientManager()

        Subscribe.topics_glob = RosbridgeWebSocketServer.topics_glob
        Advertise.topics_glob = RosbridgeWebSocketServer.topics_glob
        Publish.topics_glob = RosbridgeWebSocketServer.topics_glob
        AdvertiseService.services_glob = RosbridgeWebSocketServer.services_glob
        UnadvertiseService.services_glob = RosbridgeWebSocketServer.services_glob
        CallService.services_glob = RosbridgeWebSocketServer.services_glob

        rospy.on_shutdown(self.websocket_stop_signal)
        self.shared_data=shared_data

    def run(self):
        def handle_compression_offers(offers):
            if not self.use_compression:
                return
            for offer in offers:
                if isinstance(offer, PerMessageDeflateOffer):
                    return PerMessageDeflateOfferAccept(offer)

        if self.certfile is not None and self.keyfile is not None:
            protocol = 'wss'
            context_factory = ssl.DefaultOpenSSLContextFactory(self.keyfile, self.certfile)
        else:
            protocol = 'ws'
            context_factory = None

        uri=self.shared_data['url']
        if self.is_server:
            factory = WebSocketServerFactory(uri, externalPort=self.external_port)
            factory.protocol = RosbridgeWebSocketServer
            factory.setProtocolOptions(
                perMessageCompressionAccept=handle_compression_offers,
                autoPingInterval=self.ping_interval,
                autoPingTimeout=self.ping_timeout,
            )
            setattr(factory.protocol, 'shared_data', self.shared_data)
            connected = False
            while not connected and not rospy.is_shutdown():
                try:
                    listenWS(factory, context_factory)
                    rospy.loginfo('Rosbridge WebSocket server started at {}'.format(uri))
                    connected = True
                except CannotListenError as e:
                    rospy.logwarn("Unable to start server: " + str(e) +
                                  " Retrying in " + str(self.retry_startup_delay) + "s.")
                    rospy.sleep(self.retry_startup_delay)

            #rospy.on_shutdown(self.shutdown_hook)
            reactor.run()
        else:
            factory = MyWebSocketClientFactory(uri)
            factory.protocol = RosbridgeWebSocketClient
            setattr(factory.protocol, 'shared_data', self.shared_data)
            try:
                reactor.connectTCP(self.shared_data['IP'], int(self.shared_data['port']), factory)
                rospy.loginfo('Rosbridge WebSocket client started at {}'.format(uri))
                reactor.run()
            except CannotListenError as e:
                rospy.logwarn("Unable to start server: " + str(e) +
                              " Retrying in " + str(self.retry_startup_delay) + "s.")

    def websocket_stop_signal(self,*args):
        try:
            reactor.crash()
        except ReactorNotRunning:
            rospy.logwarn("Can't stop the reactor, it wasn't running")
