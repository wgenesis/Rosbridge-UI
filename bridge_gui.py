#!/usr/bin/env python
# -*- coding: utf-8 -*-

import sys

from twisted.python import log
from twisted.internet import reactor
from twisted.internet.error import CannotListenError, ReactorNotRunning
from autobahn.twisted.websocket import WebSocketServerFactory, listenWS,WebSocketClientFactory
log.startLogging(sys.stdout)

from utils.rosbridge_websocket import Websocket
from utils.bridge_ui import Ui_MainWindow
from PyQt5.QtWidgets import QMainWindow,QWidget,QApplication,QHeaderView,QTableWidgetItem,QAbstractItemView,QLabel
from PyQt5.QtGui import QFont
from PyQt5.QtCore import QThread,pyqtSignal,QObject,QTimer,QStringListModel,QRect
import sys
import rospy
import time
import socket
import os
import signal
from multiprocessing import Process, Queue, Manager,Value, Lock
import psutil


def get_host_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
    finally:
        s.close()
    return ip

class Message(QLabel):
    def __init__(self,parent=None):
        super(Message,self).__init__(parent)
        self.color={'red':'255,0,0','green':'0,255,0','white':'255,255,255','black':'0,0,0','blue':'0,0,255','orange':'255,153,0'}
        self.setGeometry(QRect(0, 805, 996, 25))
        font = QFont()
        font.setPointSize(15)
        self.setFont(font)
        self.setObjectName("INS_GPS_message")
        self.time=QTimer()
        self.time.timeout.connect(self.gradients)
        self.transparent=0
        self.backgroundColor='0,0,0'
        self.textColor='0,0,0'
        self.Text=''

    def colorConstraint(self,color,Type):
        if type(color) is str:
            if color in self.color:
                if Type=='background':
                    self.backgroundColor = self.color[color]
                    return True
                elif Type=='text':
                    self.textColor=self.color[color]
                    return True
                else:
                    return False
            else:
                return False
        elif type(color) is list or type(color) is tuple:
            if  len(color) == 3 and max(color) <= 255 and min(color) >= 0:
                if Type=='background':
                    self.backgroundColor = str(color)[1:-1]
                    return True
                elif Type=='text':
                    self.textColor=str(color)[1:-1]
                    return True
                else:
                    return False
            else:
                return False

    def setStatusMessage(self,Text,backgroundColor,textColor):
        self.transparent=250
        self.setText(Text)
        self.time.start(50)
        if not self.colorConstraint(backgroundColor,'background'):
            raise KeyError('颜色设置错误！')
        if not self.colorConstraint(textColor,'text'):
            raise KeyError('颜色设置错误！')

    def gradients(self):
        if self.transparent>=0:
            self.setStyleSheet('background-color:'+'rgba('+self.backgroundColor+','+str(self.transparent)+
                               ');color: rgba('+self.textColor+','+str(self.transparent)+');')
            self.transparent-=10
        else:
            self.time.stop()

def verify_IP_and_port(IP,port):
    if len(IP) and len(str(port)):
        ip=IP.split('.')
        if not ip[0].isdigit():
            ip[0]=ip[0][5:]
        for ip_ in ip:
            if not ip_.isdigit():
                return False
        if len(ip)==4 and '' not in ip:
            if int(min(ip))>=0 and int(max(ip))<=255:
                if int(port)>=0 and int(port)<=65535:
                    return True
    return False

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

class MyWindows(QMainWindow,Ui_MainWindow,QWidget):
    def __init__(self,shared_data,parent=None):
        super(Ui_MainWindow,self).__init__(parent)
        self.setupUi(self)
        #设定合适行列宽度
        self.topic_list.horizontalHeader().setStretchLastSection(True)
        self.topic_list.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.request_topic.horizontalHeader().setStretchLastSection(True)
        self.request_topic.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        #设定字体大小
        font = QFont()
        font.setPixelSize(15)
        self.topic_list.setFont(font)
        self.request_topic.setFont(font)
        #设定不可编辑
        self.topic_list.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.topic_list.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.request_topic.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.request_topic.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.server_client_list.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.current_receive.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.current_send.setEditTriggers(QAbstractItemView.NoEditTriggers)

        self.message=Message(self.centralwidget)

        self.slot_function_connect()
        self.set_address()
        self.shared_data=shared_data
        self.proc_websocket=None
        self.color={'red':'255,0,0','green':'0,255,0','white':'255,255,255','black':'0,0,0','blue':'0,0,255','orange':'255,153,0'}
        self.set_status_display(self.server_status,'white','red','未启动')
        self.set_status_display(self.client_status,'white','red','未启动')
        self.server_stop.setEnabled(False)
        self.client_stop.setEnabled(False)
        self.current_topic_list=[]
        self.current_client_list=[]
        self.topic_list_update_timer=QTimer()
        self.topic_list_update_timer.timeout.connect(self.list_update)
        self.topic_list_update_timer.start(500)
        self.client_start_connect=False
        self.client_start_time=QTimer()
        self.client_start_time.timeout.connect(self.websocket_client_connecting)

        self.topic_list_dict={}

    def list_update(self):
        current_topic_list=[]
        try:
            current_topic_list=rospy.get_published_topics()
        except Exception as e:
            print(str(e))
        if current_topic_list != self.current_topic_list and isinstance(current_topic_list,list):
            self.current_topic_list=current_topic_list
            self.topic_list.clear()
            self.topic_list.setRowCount(len(self.current_topic_list))
            for i,data in enumerate(self.current_topic_list):
                topic_name_item=QTableWidgetItem(data[0])
                topic_type_item=QTableWidgetItem(data[1])
                self.topic_list.setItem(i,0,topic_name_item)
                self.topic_list.setItem(i, 1, topic_type_item)
        if self.current_client_list != self.shared_data['clients']:
            self.current_client_list=self.shared_data['clients'].copy()
            qlist=QStringListModel()
            qlist.setStringList(self.current_client_list)
            self.server_client_list.setModel(qlist)
        #print(self.shared_data['topic_list'])
        if self.topic_list_dict!=self.shared_data['topic_list']:
            rows=0
            self.topic_list_dict=self.shared_data['topic_list'].copy()
            for op_list in self.topic_list_dict.values():
                rows+=len(op_list)
            self.request_topic.clear()
            self.request_topic.setRowCount(rows)
            its=0
            for key,data in self.topic_list_dict.items():
                if key == '9999':
                    for j,value in enumerate(data):
                        request_user=QTableWidgetItem(self.connected_server_url)
                        request_op=QTableWidgetItem(value['op'])
                        request_topic=QTableWidgetItem(value['topic'])
                        self.request_topic.setItem(its,0,request_user)
                        self.request_topic.setItem(its,1,request_op)
                        self.request_topic.setItem(its,2,request_topic)
                        its+=1
        if self.shared_data['client_connected']:
            self.set_status_display(self.client_status, 'white', 'green', '已连接')

    def slot_function_connect(self):
        self.server_begin.clicked.connect(self.websocket_server_start)
        self.server_stop.clicked.connect(self.websocket_server_stop)
        self.client_begin.clicked.connect(self.websocket_client_start)
        self.client_stop.clicked.connect(self.websocket_client_stop)

    def set_status_display(self,label,text_color,background_color,text):
        label.setStyleSheet('background-color:' + 'rgb(' + self.color[background_color] + ');color: rgb(' + self.color[text_color] + ');')
        label.setText(text)

    def set_address(self):
        self.server_IP_select.addItem(get_host_ip())
        self.server_IP_select.addItem('127.0.0.1')

    def websocket_server_stop(self):
        try:
            os.kill(self.shared_data['websocket_pid'],signal.SIGUSR1)
        except:
            pass
        time.sleep(0.5)
        self.proc_websocket.terminate()
        self.set_status_display(self.server_status, 'white', 'red', '未启动')
        self.client.setEnabled(True)
        self.server_begin.setEnabled(True)
        self.server_stop.setEnabled(False)
        self.server_url.setText(' ')
        self.shared_data['start_websocket'] = False
        self.shared_data['clients']=[]

    def websocket_server_start(self):
        url='ws://'+self.server_IP_select.currentText().split('//')[-1]+':'+self.server_port.text()
        self.server_url.setText(url)
        self.shared_data['url']=url
        self.client.setEnabled(False)
        self.set_status_display(self.server_status,'white','orange','启动中')
        try:
            self.shared_data['start_websocket']=True
            self.proc_websocket=Process(target=websoket_server_process,args=(data,),daemon=True)
            self.proc_websocket.start()
            self.set_status_display(self.server_status, 'white', 'green', '已启动')
            self.server_begin.setEnabled(False)
            self.server_stop.setEnabled(True)
        except:
            self.shared_data['start_websocket'] = False
            self.set_status_display(self.server_status, 'white', 'red', '未启动')
            self.client.setEnabled(True)

    def websocket_client_stop(self):
        if int(self.shared_data['websocket_pid']) in psutil.pids():
            os.kill(self.shared_data['websocket_pid'],signal.SIGUSR1)
        time.sleep(0.5)
        self.proc_websocket.terminate()
        self.shared_data['client_connected'] = False
        self.set_status_display(self.client_status, 'white', 'red', '未连接')
        self.server.setEnabled(True)
        self.client_begin.setEnabled(True)
        self.client_stop.setEnabled(False)
        self.client_url.setText(' ')
        self.shared_data['start_websocket'] = False

        if self.client_start_time.isActive():
            self.client_start_time.stop()
        self.client_start_connect=False

    def websocket_client_connecting(self):
        self.set_status_display(self.client_status, 'white', 'orange', '连接中')
        if self.client_start_connect :
            if not self.shared_data['client_connected']:
                self.proc_websocket = Process(target=websocket_client_process, args=(data,), daemon=True)
                self.proc_websocket.start()
            else:
                self.set_status_display(self.client_status, 'white', 'green', '已连接')
                self.client_start_connect = False
        else :
            self.client_start_time.stop()

    def websocket_client_start(self):
        if not (len(self.client_IP.text()) and len(self.client_port.text())) :
            self.message.setStatusMessage('请设置IP和端口','red','white')
        elif not verify_IP_and_port(self.client_IP.text(),self.client_port.text()):
            self.message.setStatusMessage('请设置正确的IP和端口', 'red', 'white')
        else:
            self.client_start_connect=True
            self.connected_server_url=self.client_IP.text().split('//')[-1] + ':' + self.client_port.text()
            url = 'ws://' + self.connected_server_url
            self.client_url.setText(url)
            self.shared_data['url'] = url
            self.shared_data['IP'] = self.client_IP.text().split('//')[-1]
            self.shared_data['port'] = self.client_port.text()
            self.server.setEnabled(False)
            self.client_begin.setEnabled(False)
            self.client_stop.setEnabled(True)
            # self.client_start_time.start(500)
            self.proc_websocket = Process(target=websocket_client_process, args=(data,), daemon=True)
            self.proc_websocket.start()

def websoket_server_process(data):
    rospy.init_node("server_bridge")
    websocket = Websocket(data)
    data['websocket_pid']=os.getpid()
    signal.signal(signal.SIGUSR1, websocket.websocket_stop_signal)
    websocket.run()
    os.kill(os.getpid(), signal.SIGTERM)

def websocket_client_process(data):
    rospy.init_node("client_bridge")
    websocket = Websocket(data,False)
    data['websocket_pid']=os.getpid()
    signal.signal(signal.SIGUSR1, websocket.websocket_stop_signal)
    websocket.run()
    os.kill(os.getpid(), signal.SIGTERM)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    manager=Manager()
    data=manager.dict()
    data['url']=None
    data['IP']=None
    data['port']=None
    data['websocket_pid']=None
    data['start_websocket']=None
    data['clients']=[]
    data['client_connected']=False
    data['topic_list']={}
    my_win = MyWindows(data)
    my_win.show()
    app.exec_()
    try:
        my_win.proc_websocket.terminate()
    except:
        pass
    sys.exit(0)
