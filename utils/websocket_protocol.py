#!/usr/bin/env python
# -*- coding: utf-8 -*-
# @File     : websocket_protocol.py
# @Project  : Sleipnir
# @Software : PyCharm
# @Author   : why
# @Email    : weihaoyuan2@126.com
# @Time     : 2020/9/25 上午10:45

import rospy
import uuid

import sys
import threading
import traceback
from functools import wraps
from collections import deque
from rosauth.srv import Authentication

from autobahn.twisted.websocket import WebSocketServerProtocol,WebSocketClientProtocol
from twisted.internet import interfaces, reactor
from zope.interface import implementer

from rosbridge_library.rosbridge_protocol import RosbridgeProtocol
from rosbridge_library.util import json, bson
import json
def _log_exception():
    """Log the most recent exception to ROS."""
    exc = traceback.format_exception(*sys.exc_info())
    rospy.logerr(''.join(exc))


def log_exceptions(f):
    """Decorator for logging exceptions to ROS."""

    @wraps(f)
    def wrapper(*args, **kwargs):
        try:
            return f(*args, **kwargs)
        except:
            _log_exception()
            raise

    return wrapper


class IncomingQueue(threading.Thread):
    """
    将传入消息与authbahn线程分离。
    这可以减轻传出消息被传入消息阻止的情况，反之亦然。
    """

    def __init__(self, protocol):
        threading.Thread.__init__(self)
        self.daemon = True
        self.queue = deque()
        self.protocol = protocol

        self.cond = threading.Condition()
        self._finished = False

    def finish(self):
        """清除队列，不接受进一步的消息。"""
        with self.cond:
            self._finished = True
            while len(self.queue) > 0:
                self.queue.popleft()
            self.cond.notify()

    def push(self, msg):
        """消息进入队列，当有消息入列时发出notify"""
        with self.cond:
            self.queue.append(msg)
            self.cond.notify()

    def run(self):
        """
        线程执行程序
        """
        while True:
            # 当有消息入列时执行协议incoming，发送ros消息
            with self.cond:
                if len(self.queue) == 0 and not self._finished:
                    self.cond.wait()
                if self._finished:
                    break
                msg = self.queue.popleft()
            self.protocol.incoming(msg)
        self.protocol.finish()


@implementer(interfaces.IPushProducer)
class OutgoingValve:
    """
    允许autobahn暂停传输来自rosbridge的传出消息。
    """
    def __init__(self, proto):
        self._proto = proto
        self._valve = threading.Event()
        self._finished = False

    @log_exceptions
    def relay(self, message):
        self._valve.wait()
        if self._finished:
            return
        reactor.callFromThread(self._proto.outgoing, message)

    def pauseProducing(self):
        if not self._finished:
            self._valve.clear()

    def resumeProducing(self):
        self._valve.set()

    def stopProducing(self):
        self._finished = True
        self._valve.set()

class RosbridgeWebSocketServer(WebSocketServerProtocol):
    client_id_seed = 0
    clients_connected = 0
    authenticate = False

    # The following are passed on to RosbridgeProtocol
    # defragmentation.py:
    fragment_timeout = 600                  # seconds
    # protocol.py:
    delay_between_messages = 0              # seconds
    max_message_size = None                 # bytes
    unregister_timeout = 10.0               # seconds
    bson_only_mode = False

    # Get the glob strings and parse them as arrays.
    topics_glob = []
    services_glob = []
    params_glob = []

    #连接成功时调用
    def onOpen(self):
        cls = self.__class__
        parameters = {
            "fragment_timeout": cls.fragment_timeout,
            "delay_between_messages": cls.delay_between_messages,
            "max_message_size": cls.max_message_size,
            "unregister_timeout": cls.unregister_timeout,
            "bson_only_mode": cls.bson_only_mode
        }
        try:
            #创建客户端传输协议
            self.protocol = RosbridgeProtocol(cls.client_id_seed,parameters=parameters)
            #创建消息队列
            self.incoming_queue = IncomingQueue(self.protocol)
            #启动队列线程
            self.incoming_queue.start()
            #创建用于暂停传输的对象
            producer = OutgoingValve(self)
            self.transport.registerProducer(producer, True)
            producer.resumeProducing()
            self.protocol.outgoing = producer.relay
            self.authenticated = False
            # 生成客户端随机id
            cls.client_id_seed += 1
            cls.clients_connected += 1
            self.client_id = uuid.uuid4()
            self.peer = self.transport.getPeer().host
            self.client_url=str(self.peer)+':'+str(self.transport.getPeer().port)
            print('*'*10,self.client_url)
            client_list=self.shared_data['clients'].copy()
            client_list.append(self.client_url)
            self.shared_data['clients']=client_list.copy()
            #添加客户端到管理器
            if cls.client_manager:
                cls.client_manager.add_client(self.client_id, self.peer)

        except Exception as exc:
            rospy.logerr("Unable to accept incoming connection.  Reason: %s", str(exc))
        rospy.loginfo("Client connected.  %d clients total.", cls.clients_connected)


    #接收到消息时调用
    def onMessage(self, message, binary):
        cls = self.__class__
        if not binary:
            message = message.decode('utf-8')
        if cls.authenticate and not self.authenticated:
            try:
                if cls.bson_only_mode:
                    msg = bson.BSON(message).decode()
                else:
                    msg = json.loads(message)

                if msg['op'] == 'auth':
                    auth_srv = rospy.ServiceProxy('authenticate', Authentication)
                    resp = auth_srv(msg['mac'], msg['client'], msg['dest'],
                                    msg['rand'], rospy.Time(msg['t']), msg['level'],
                                    rospy.Time(msg['end']))
                    self.authenticated = resp.authenticated
                    if self.authenticated:
                        rospy.loginfo("Client %d has authenticated.", self.protocol.client_id)
                        return
                rospy.logwarn("Client %d did not authenticate. Closing connection.",
                              self.protocol.client_id)
                self.sendClose()
            except:
                self.incoming_queue.push(message)
        else:
            self.incoming_queue.push(message)

    def outgoing(self, message):
        if type(message) == bson.BSON:
            binary = True
            message = bytes(message)
        elif type(message) == bytearray:
            binary = True
            message = bytes(message)
        else:
            binary = False
            message = message.encode('utf-8')

        self.sendMessage(message, binary)

    #链接断开时调用
    def onClose(self, was_clean, code, reason):
        client_list=self.shared_data['clients'].copy()
        client_list.remove(self.client_url)
        self.shared_data['clients']=client_list.copy()
        if not hasattr(self, 'protocol'):
            return
        cls = self.__class__
        cls.clients_connected -= 1

        if cls.client_manager:
            cls.client_manager.remove_client(self.client_id, self.peer)
        rospy.loginfo("Client disconnected. %d clients total.", cls.clients_connected)
        self.incoming_queue.finish()

class RosbridgeWebSocketClient(WebSocketClientProtocol):
    client_id_seed = 0
    clients_connected = 0
    fragment_timeout = 600                  # seconds
    # protocol.py:
    delay_between_messages = 0              # seconds
    max_message_size = None                 # bytes
    unregister_timeout = 10.0               # seconds
    bson_only_mode = False

    topics_glob = []
    services_glob = []
    params_glob = []

    #连接成功时调用
    def onOpen(self):
        cls = self.__class__
        parameters = {
            "fragment_timeout": cls.fragment_timeout,
            "delay_between_messages": cls.delay_between_messages,
            "max_message_size": cls.max_message_size,
            "unregister_timeout": cls.unregister_timeout,
            "bson_only_mode": cls.bson_only_mode
        }
        try:
            #创建客户端传输协议
            self.protocol = RosbridgeProtocol(9999,parameters=parameters)
            #创建消息队列
            self.incoming_queue = IncomingQueue(self.protocol)
            #启动队列线程
            self.incoming_queue.start()
            #创建用于暂停传输的对象
            producer = OutgoingValve(self)
            self.transport.registerProducer(producer, True)
            producer.resumeProducing()
            self.protocol.outgoing = producer.relay
            self.shared_data['client_connected']=True
        except Exception as exc:
            rospy.logerr("Unable to accept incoming connection.  Reason: %s", str(exc))
        rospy.loginfo("Connection succeeded")

    #接收到消息时调用
    def onMessage(self, message, binary):
        if not binary:
            message = message.decode('utf-8')
        self.incoming_queue.push(message)
        print(message)

    def outgoing(self, message):
        if type(message) == bytearray:
            binary = True
            message = bytes(message)
        else:
            binary = False
            message = message.encode('utf-8')
        self.sendMessage(message, binary)

    #链接断开时调用
    def onClose(self, was_clean, code, reason):
        if not hasattr(self, 'protocol'):
            return
        cls = self.__class__
        cls.clients_connected -= 1
        rospy.loginfo("Client disconnected. %d clients total.", cls.clients_connected)
        self.shared_data['client_connected'] = False
        self.incoming_queue.finish()
