"""
消息队列监听模块
@Version: 1.8
@Description: 实现监听消息队列并转发消息到WXApi的功能
@Author: Isaac
@Date: 2025-05-15
"""

import json
import time
import logging
import pika
import os
import asyncio
import requests
from threading import Thread
import argparse
import base64

# 引入项目内部的微信API客户端
from WechatAPI import WechatAPIClient

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('mq_listener.log')
    ]
)
logger = logging.getLogger('mq_listener')


# 读取项目配置
def load_config():
    try:
        config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "main_config.toml")

        # 尝试多种方式加载TOML文件
        try:
            # 尝试使用tomli (Python 3.11+内置库)
            import tomllib
            with open(config_path, "rb") as f:
                return tomllib.load(f)
        except ImportError:
            try:
                # 尝试使用tomli库
                import tomli
                with open(config_path, "rb") as f:
                    return tomli.load(f)
            except ImportError:
                # 简单解析方式，仅解析Protocol版本
                config = {"Protocol": {"version": "849"}}  # 默认值
                with open(config_path, "r", encoding="utf-8") as f:
                    for line in f:
                        if line.strip().startswith("version"):
                            parts = line.split("=")
                            if len(parts) >= 2:
                                version = parts[1].strip().strip('"').strip("'")
                                config["Protocol"]["version"] = version
                                break
                return config

    except Exception as e:
        logger.error(f"加载main_config.toml失败: {e}")
        # 返回默认配置
        return {"Protocol": {"version": "849"}}


class WXAdapter:
    """微信适配器，负责将消息队列的数据格式转换为项目内部格式并发送"""

    def __init__(self):
        """初始化微信适配器"""
        # 从项目配置获取IP和端口
        main_config = load_config()
        protocol_version = main_config.get("Protocol", {}).get("version", "849")

        # 设置API服务器地址
        self.api_ip = "127.0.0.1"
        self.api_port = 9011  # 使用9091端口的API

        logger.info(f"使用协议版本: {protocol_version}, 使用API服务地址: {self.api_ip}:{self.api_port}")

        # 初始化API客户端 - 仅用于数据库访问
        self.wx_client = WechatAPIClient("127.0.0.1", 9091)
        self.friends_cache = {}  # 好友wxid缓存
        self.groups_cache = {}  # 群聊wxid缓存
        self.members_cache = {}  # 群成员wxid缓存

        # 初始化手动映射表
        self._init_manual_mapping()

        # 从数据库直接获取当前登录的微信号
        self.my_wxid = 'wxid_nmoq1pfooveu12'
        if not self.my_wxid:
            logger.warning("从数据库获取微信号失败，尝试其他方式获取")
            self._try_get_wxid_from_api()

        logger.info(f"当前登录微信号: {self.my_wxid}")

        # 获取可用方法列表用于调试
        self._get_available_methods()

        # 初始化时尝试获取好友和群聊列表
        self.refresh_cache()

        # 启动定时刷新缓存线程
        self._start_auto_refresh()

    def _init_manual_mapping(self):
        """初始化手动映射表"""
        # 用户映射表
        self.user_mapping = {
            "朱欣园": "wxid_a2osv6hgqm2i22",
            "王鑫勤": "wxid_x9dchbcdhql921",
            "陈佳莹": "wxid_ld8l5dmp3m4421",
            "徐梦圆": "wxid_wl0lcqr48pso11",
            "刘尧": "wxid_o95guka3ip4712",
            "吴丹丹": "wxid_ju92hrjst7tu12",
            "金思涵": "wxid_xpwiv4vmyj9722",
            "罗晓彤": "a1137161419",
            "厉巧云": "lqy_962464",
            "戴婕": "wxid_9noyjoj7znzz22",
            "傅莹莹": "wxid_c4qglt261sxe22",
            "林雪飞": "wxid_p87myd7tfcat22",
            "陈玲玲": "chenlingling6048",
            "赵浩然": "lekey22",
            "陈嘉赢": "chenjiay_490986969",
            "刘非凡": "wxid_vom8gnhayr2o11",
            "余本鑫": "wxid_oqu2t4xtckug22",
            "李叙洁": "wxid_s6g0b8zv0qx722",
            "黄嘉施": "wxid_1lf38uxmia6622",
            "毛怡燕": "maoyi1992",
            "向金凯": "xiang_kai163"
            # 更多用户映射...
        }

        # 群聊映射表
        self.group_mapping = {
            "测试群": "43256124689@chatroom",
            "测试发送消息": "52324230765@chatroom",
            "欧盟通知群": "58103688366@chatroom",
            "北美通知群": "57000892820@chatroom",
            "Web性能监控群": "53068513019@chatroom",
            "自动化通知群": "49629678176@chatroom",
            "幸福一家人👨‍👩‍👧‍👧": "6872717936@chatroom",
            "Costway IT大家庭": "47881484208@chatroom"
            # 更多群聊映射...
        }

        # 将映射表同步到缓存
        self.friends_cache.update(self.user_mapping)
        self.groups_cache.update(self.group_mapping)

        logger.info(f"已初始化手动映射表 - 用户: {len(self.user_mapping)}个, 群聊: {len(self.group_mapping)}个")

    def _get_my_wxid_from_db(self):
        """从数据库获取当前登录的微信号"""
        try:
            # 尝试连接到我的信息数据库
            conn = self.wx_client.get_contacts_db()
            if conn:
                cursor = conn.cursor()

                # 查询我的微信号信息
                cursor.execute("SELECT wxid FROM my_info LIMIT 1")
                result = cursor.fetchone()

                if result and result[0]:
                    return result[0]

                # 如果上面的查询失败，尝试另一种方式
                cursor.execute("SELECT value FROM system_settings WHERE key = 'wxid' LIMIT 1")
                result = cursor.fetchone()

                if result and result[0]:
                    return result[0]

                # 如果以上方法都失败，返回一个默认值或空字符串
                logger.warning("无法从数据库获取微信号，将使用空字符串")
                return ""
            else:
                logger.error("无法连接到数据库")
                return ""
        except Exception as e:
            logger.error(f"从数据库获取微信号时发生异常: {e}")
            return ""

    def _try_get_wxid_from_api(self):
        """尝试通过API直接获取wxid"""
        try:
            # 直接调用API获取wxid
            url = f'http://{self.api_ip}:{self.api_port}/VXAPI/Login/GetSelf'
            response = requests.get(url, timeout=5)
            if response.status_code == 200:
                result = response.json()
                if result.get("Success"):
                    self.my_wxid = result.get("Data", {}).get("Wxid")
                    logger.info(f"通过API获取到wxid: {self.my_wxid}")
                else:
                    logger.error(f"API获取wxid失败: {result.get('Message')}")
            else:
                logger.error(f"调用API获取wxid失败，状态码: {response.status_code}")
        except Exception as e:
            logger.error(f"通过API获取wxid时出错: {e}")

    def _get_available_methods(self):
        """获取可用的API方法列表用于调试"""
        try:
            all_methods = [method for method in dir(self.wx_client) if not method.startswith('_')]
            contact_methods = [method for method in all_methods if 'contact' in method.lower()]
            friend_methods = [method for method in all_methods if 'friend' in method.lower()]
            room_methods = [method for method in all_methods if
                            'room' in method.lower() or 'chatroom' in method.lower()]
            message_methods = [method for method in all_methods if
                               'message' in method.lower() or 'msg' in method.lower() or 'send' in method.lower()]

            logger.info(f"联系人相关方法: {contact_methods}")
            logger.info(f"好友相关方法: {friend_methods}")
            logger.info(f"群聊相关方法: {room_methods}")
            logger.info(f"消息相关方法: {message_methods}")
        except Exception as e:
            logger.error(f"获取可用方法时发生异常: {e}")

    def refresh_cache(self):
        """刷新好友和群聊缓存"""
        try:
            # 获取好友列表
            self._update_friends_cache()

            # 获取群聊列表
            self._update_groups_cache()

            logger.info("成功刷新好友和群聊缓存")
        except Exception as e:
            logger.error(f"刷新缓存失败: {e}")

    def _update_friends_cache(self):
        """更新好友缓存，采用先获取列表再获取详情的标准流程"""
        try:
            # 1. 先调用GetContractList获取所有联系人wxid
            url = f'http://{self.api_ip}:{self.api_port}/VXAPI/Friend/GetContractList'
            json_param = {
                "Wxid": self.my_wxid,
                "CurrentWxcontactSeq": 0,
                "CurrentChatroomContactSeq": 0
            }
            response = requests.post(url, json=json_param, timeout=10)

            if response.status_code == 200:
                result = response.json()
                if result.get("Success"):
                    logger.info("成功获取联系人列表")
                    # 筛选个人联系人wxid
                    contact_list = []
                    for contact in result.get("Data", {}).get("ContactList", []):
                        if isinstance(contact, dict) and "@chatroom" not in contact.get("UserName", ""):
                            contact_list.append(contact.get("UserName"))

                    logger.info(f"筛选出 {len(contact_list)} 个非群聊联系人")

                    # 2. 调用GetContractDetail获取联系人详情
                    if contact_list:
                        # 由于可能联系人较多，分批获取详情
                        batch_size = 20  # 每次请求最多20个联系人
                        count = 0

                        for i in range(0, len(contact_list), batch_size):
                            batch = contact_list[i:i + batch_size]

                            url = f'http://{self.api_ip}:{self.api_port}/VXAPI/Friend/GetContractDetail'
                            json_param = {"Wxid": self.my_wxid, "Towxids": ",".join(batch), "Chatroom": ""}
                            response = requests.post(url, json=json_param, timeout=10)

                            if response.status_code == 200:
                                details = response.json()
                                if details.get("Success"):
                                    # 3. 更新缓存
                                    for contact in details.get("Data", {}).get("ContactList", []):
                                        wxid = contact.get("UserName")
                                        # 处理不同格式的昵称和备注
                                        nickname = ""
                                        remark = ""

                                        if "NickName" in contact:
                                            if isinstance(contact["NickName"], dict) and "string" in contact[
                                                "NickName"]:
                                                nickname = contact["NickName"]["string"]
                                            elif isinstance(contact["NickName"], str):
                                                nickname = contact["NickName"]

                                        if "RemarkName" in contact:
                                            if isinstance(contact["RemarkName"], dict) and "string" in contact[
                                                "RemarkName"]:
                                                remark = contact["RemarkName"]["string"]
                                            elif isinstance(contact["RemarkName"], str):
                                                remark = contact["RemarkName"]

                                        if wxid and (nickname or remark):
                                            if nickname:
                                                self.friends_cache[nickname] = wxid
                                                count += 1
                                            if remark:
                                                self.friends_cache[remark] = wxid
                                                if not nickname:  # 只有在没有昵称时才计数备注
                                                    count += 1
                                else:
                                    logger.error(f"获取联系人详情失败: {details.get('Message', '未知错误')}")
                            else:
                                logger.error(f"调用GetContractDetail失败，状态码: {response.status_code}")

                        logger.info(f"更新好友缓存完成，共 {count} 个联系人")
                    else:
                        logger.warning("没有找到个人联系人")
                else:
                    logger.error(f"获取联系人列表失败: {result.get('Message', '未知错误')}")
                    # 使用备选方法
                    self._load_friends_from_db()
            else:
                logger.error(f"调用GetContractList失败，状态码: {response.status_code}")
                # 使用备选方法
                self._load_friends_from_db()
        except Exception as e:
            logger.error(f"更新好友缓存时发生异常: {e}")
            # 使用备选方法
            self._load_friends_from_db()

    def _load_friends_from_db(self):
        """从数据库加载好友信息"""
        try:
            conn = self.wx_client.get_contacts_db()
            if conn:
                cursor = conn.cursor()

                # 查询个人联系人信息
                cursor.execute("""
                SELECT wxid, nickname, remark FROM contacts 
                WHERE wxid NOT LIKE '%@chatroom' AND nickname IS NOT NULL
                """)

                rows = cursor.fetchall()

                count = 0
                for row in rows:
                    wxid = row[0]
                    nickname = row[1]
                    remark = row[2]

                    if wxid and nickname:
                        self.friends_cache[nickname] = wxid
                        count += 1
                        # 如果有备注名，也添加到缓存
                        if remark:
                            self.friends_cache[remark] = wxid

                logger.info(f"从数据库加载了 {count} 个联系人")
        except Exception as e:
            logger.error(f"从数据库加载联系人信息失败: {e}")

    def _update_groups_cache(self):
        """更新群聊缓存"""
        try:
            # 直接通过API获取群聊列表
            try:
                url = f'http://{self.api_ip}:{self.api_port}/VXAPI/Group/GetChatroomList'
                json_param = {"Wxid": self.my_wxid}
                response = requests.post(url, json=json_param, timeout=10)

                if response.status_code == 200:
                    json_resp = response.json()
                    if json_resp.get("Success"):
                        rooms_result = json_resp.get("Data")
                        logger.info("成功通过API获取群聊列表")
                    else:
                        logger.error(f"API获取群聊失败: {json_resp.get('Message')}")
                        rooms_result = None
                else:
                    logger.error(f"调用API获取群聊失败，状态码: {response.status_code}")
                    rooms_result = None
            except Exception as e:
                logger.error(f"通过API获取群聊列表失败: {e}")
                rooms_result = None

            # 如果没有找到可用方法，记录更多调试信息
            if not rooms_result:
                logger.error("无法通过API获取群聊列表")
                # 尝试从数据库加载群聊信息作为备用
                self._load_groups_from_db()
                return

            # 处理不同格式的返回结果
            rooms = []
            if isinstance(rooms_result, dict):
                if "List" in rooms_result:
                    rooms = rooms_result["List"]
                elif "data" in rooms_result:
                    rooms = rooms_result["data"]
                elif "Data" in rooms_result:
                    rooms = rooms_result["Data"]
                elif "ContactList" in rooms_result:
                    rooms = rooms_result["ContactList"]
            elif isinstance(rooms_result, list):
                rooms = rooms_result

            logger.info(f"处理后的群聊数据: {rooms[:3]}...")  # 只显示前3个避免日志过长

            # 遍历群聊数据，提取信息
            count = 0
            for room in rooms:
                # 检查是否为群聊
                wxid = None
                nickname = None

                if isinstance(room, dict):
                    # 提取不同格式中的wxid和名称
                    if "wxid" in room:
                        wxid = room["wxid"]
                    elif "Wxid" in room:
                        wxid = room["Wxid"]
                    elif "UserName" in room:
                        wxid = room["UserName"]

                    if "nickname" in room:
                        nickname = room["nickname"]
                    elif "NickName" in room:
                        nickname = room["NickName"]
                    elif "Nickname" in room:
                        nickname = room["Nickname"]
                    elif "DisplayName" in room:
                        nickname = room["DisplayName"]

                # 只处理群聊
                if wxid and "@chatroom" in wxid and nickname:
                    self.groups_cache[nickname] = wxid
                    count += 1
                    # 也添加格式化后的名称作为备用
                    formatted_name = nickname.strip()
                    if formatted_name != nickname:
                        self.groups_cache[formatted_name] = wxid

            logger.info(f"更新群聊缓存完成，共 {count} 条记录")
            logger.info(f"群聊缓存内容: {self.groups_cache}")

            # 如果群聊缓存为空，尝试从数据库加载
            if count == 0:
                self._load_groups_from_db()

        except Exception as e:
            logger.error(f"更新群聊缓存时发生异常: {e}")
            # 出现异常时，尝试从数据库加载群聊信息
            self._load_groups_from_db()

    def _load_groups_from_db(self):
        """从数据库加载群聊信息作为备用方案"""
        try:
            # 尝试连接到contacts.db
            conn = self.wx_client.get_contacts_db()
            if conn:
                cursor = conn.cursor()
                # 查询群聊信息
                cursor.execute(
                    "SELECT DISTINCT group_wxid, group_name FROM group_members WHERE group_wxid LIKE '%@chatroom' AND group_name IS NOT NULL")
                rows = cursor.fetchall()

                count = 0
                for row in rows:
                    if row[0] and row[1]:  # 确保ID和名称都不为空
                        self.groups_cache[row[1]] = row[0]
                        count += 1

                logger.info(f"从数据库加载了 {count} 个群聊信息")
                logger.info(f"数据库群聊缓存内容: {self.groups_cache}")
        except Exception as e:
            logger.error(f"从数据库加载群聊信息失败: {e}")

    def _update_group_members(self, group_wxid):
        """更新指定群聊的成员缓存"""
        try:
            # 使用API获取群成员
            url = f'http://{self.api_ip}:{self.api_port}/VXAPI/Group/GetChatroomMemberDetail'
            json_param = {"Wxid": self.my_wxid, "QID": group_wxid}

            try:
                response = requests.post(url, json=json_param, timeout=10)

                if response.status_code == 200:
                    json_resp = response.json()
                    if json_resp.get("Success"):
                        members_result = json_resp.get("Data")
                        logger.info(f"通过API成功获取群 {group_wxid} 成员")
                    else:
                        logger.error(f"API获取群成员失败: {json_resp.get('Message')}")
                        members_result = None
                else:
                    logger.error(f"调用API获取群成员失败，状态码: {response.status_code}")
                    members_result = None
            except Exception as e:
                logger.error(f"通过API获取群成员时出错: {e}")
                members_result = None

            # 如果没有找到可用方法，尝试数据库查询
            if not members_result:
                logger.error(f"无法通过API获取群 {group_wxid} 成员")
                return self._load_group_members_from_db(group_wxid)

            # 处理不同格式的返回结果
            members = []
            if isinstance(members_result, dict):
                if "NewChatroomData" in members_result and "ChatRoomMember" in members_result["NewChatroomData"]:
                    members = members_result["NewChatroomData"]["ChatRoomMember"]
                elif "List" in members_result:
                    members = members_result["List"]
                elif "MemberList" in members_result:
                    members = members_result["MemberList"]
            elif isinstance(members_result, list):
                members = members_result

            logger.info(f"群 {group_wxid} 成员数据: {members[:3]}...")  # 只显示前3个成员

            # 初始化群组成员字典
            if group_wxid not in self.members_cache:
                self.members_cache[group_wxid] = {}

            # 遍历群成员数据，提取信息
            count = 0
            for member in members:
                wxid = None
                nickname = None
                display_name = None

                if isinstance(member, dict):
                    # 提取不同格式中的数据
                    if "wxid" in member:
                        wxid = member["wxid"]
                    elif "Wxid" in member:
                        wxid = member["Wxid"]
                    elif "UserName" in member:
                        wxid = member["UserName"]

                    if "nickname" in member:
                        nickname = member["nickname"]
                    elif "NickName" in member:
                        nickname = member["NickName"]
                    elif "Nickname" in member:
                        nickname = member["Nickname"]

                    # 群内显示名
                    if "displayname" in member:
                        display_name = member["displayname"]
                    elif "DisplayName" in member:
                        display_name = member["DisplayName"]

                if wxid and (nickname or display_name):
                    # 优先使用群显示名，其次使用昵称
                    if display_name:
                        self.members_cache[group_wxid][display_name] = wxid
                        count += 1
                    if nickname:
                        self.members_cache[group_wxid][nickname] = wxid
                        if not display_name:  # 如果没有显示名，才计数
                            count += 1

            logger.info(f"更新群 {group_wxid} 成员缓存完成，共 {count} 条记录")

            # 如果没有找到成员，尝试从数据库加载
            if count == 0:
                self._load_group_members_from_db(group_wxid)

        except Exception as e:
            logger.error(f"更新群成员缓存时发生异常: {e}")
            # 出现异常时，尝试从数据库加载群成员
            self._load_group_members_from_db(group_wxid)

    def _load_group_members_from_db(self, group_wxid):
        """从数据库加载群成员信息"""
        try:
            # 尝试连接到contacts.db
            conn = self.wx_client.get_contacts_db()
            if conn:
                cursor = conn.cursor()

                # 查询群成员信息
                cursor.execute("""
                SELECT member_wxid, nickname, display_name FROM group_members 
                WHERE group_wxid = ?
                """, (group_wxid,))

                rows = cursor.fetchall()

                # 初始化成员字典
                if group_wxid not in self.members_cache:
                    self.members_cache[group_wxid] = {}

                count = 0
                for row in rows:
                    member_wxid = row[0]
                    nickname = row[1]
                    display_name = row[2]

                    if member_wxid:
                        # 优先使用群显示名，其次使用昵称
                        if display_name:
                            self.members_cache[group_wxid][display_name] = member_wxid
                            count += 1
                        if nickname:
                            self.members_cache[group_wxid][nickname] = member_wxid
                            if not display_name:  # 如果没有显示名，才计数
                                count += 1

                logger.info(f"从数据库加载了群 {group_wxid} 的 {count} 名成员")
                return True
        except Exception as e:
            logger.error(f"从数据库加载群成员信息失败: {e}")

        return False

    def find_friend_wxid(self, friend_name):
        """通过好友名称查找wxid"""
        # 首先从缓存中查找
        if friend_name in self.friends_cache:
            logger.info(f"在缓存中找到好友 {friend_name} 的wxid: {self.friends_cache[friend_name]}")
            return self.friends_cache[friend_name]

        # 缓存中没有，尝试模糊匹配
        logger.info(f"缓存中未找到好友 {friend_name}，尝试模糊匹配")
        for cache_name, wxid in self.friends_cache.items():
            if (friend_name in cache_name) or (cache_name in friend_name):
                logger.info(f"模糊匹配成功: 用户查询 '{friend_name}' 匹配到缓存名称 '{cache_name}', wxid: {wxid}")
                # 添加到缓存以备将来使用
                self.friends_cache[friend_name] = wxid
                return wxid

        # 模糊匹配失败，尝试刷新缓存
        logger.info(f"模糊匹配失败，尝试刷新好友缓存")
        self._update_friends_cache()

        # 再次从缓存中查找
        if friend_name in self.friends_cache:
            logger.info(f"刷新缓存后找到好友 {friend_name} 的wxid: {self.friends_cache[friend_name]}")
            return self.friends_cache[friend_name]

        # 再次尝试模糊匹配
        for cache_name, wxid in self.friends_cache.items():
            if (friend_name in cache_name) or (cache_name in friend_name):
                logger.info(f"刷新缓存后模糊匹配成功: '{friend_name}' 匹配到 '{cache_name}', wxid: {wxid}")
                self.friends_cache[friend_name] = wxid
                return wxid

        # 尝试从数据库中查找
        db_wxid = self._find_friend_wxid_from_db(friend_name)
        if db_wxid:
            self.friends_cache[friend_name] = db_wxid
            return db_wxid

        logger.warning(f"未找到好友 {friend_name} 的wxid，所有匹配方法均失败")
        return None

    def _find_friend_wxid_from_db(self, friend_name):
        """从数据库查找好友wxid"""
        try:
            conn = self.wx_client.get_contacts_db()
            if conn:
                cursor = conn.cursor()

                # 先尝试精确匹配
                cursor.execute("""
                SELECT wxid, nickname, remark FROM contacts 
                WHERE nickname = ? OR remark = ?
                """, (friend_name, friend_name))

                row = cursor.fetchone()
                if row and row[0]:
                    logger.info(f"从数据库精确匹配到好友: {friend_name}, wxid: {row[0]}")
                    return row[0]

                # 再尝试模糊匹配
                cursor.execute("""
                SELECT wxid, nickname, remark FROM contacts 
                WHERE nickname LIKE ? OR remark LIKE ?
                """, (f"%{friend_name}%", f"%{friend_name}%"))

                rows = cursor.fetchall()
                if rows and len(rows) > 0:
                    for row in rows:
                        if row[0]:
                            logger.info(
                                f"从数据库模糊匹配到好友: {friend_name} 匹配到 {row[1] or row[2]}, wxid: {row[0]}")
                            return row[0]

                logger.info(f"在数据库中未找到好友 {friend_name}")
        except Exception as e:
            logger.error(f"从数据库查找好友wxid失败: {e}")

        return None

    def find_group_wxid(self, group_name):
        """通过群聊名称查找wxid"""
        # 首先从缓存中查找
        if group_name in self.groups_cache:
            logger.info(f"在缓存中找到群聊 {group_name} 的wxid: {self.groups_cache[group_name]}")
            return self.groups_cache[group_name]

        # 缓存中没有，尝试模糊匹配
        logger.info(f"缓存中未找到群聊 {group_name}，尝试模糊匹配")
        for cache_name, wxid in self.groups_cache.items():
            # 检查名称是否包含子字符串
            if (group_name in cache_name) or (cache_name in group_name):
                logger.info(f"模糊匹配成功: 用户查询 '{group_name}' 匹配到缓存名称 '{cache_name}', wxid: {wxid}")
                # 添加到缓存以备将来使用
                self.groups_cache[group_name] = wxid
                return wxid

        # 模糊匹配失败，尝试刷新缓存
        logger.info(f"模糊匹配失败，尝试刷新群聊缓存")
        self._update_groups_cache()

        # 再次从缓存中查找
        if group_name in self.groups_cache:
            logger.info(f"刷新缓存后找到群聊 {group_name} 的wxid: {self.groups_cache[group_name]}")
            return self.groups_cache[group_name]

        # 再次尝试模糊匹配
        for cache_name, wxid in self.groups_cache.items():
            if (group_name in cache_name) or (cache_name in group_name):
                logger.info(f"刷新缓存后模糊匹配成功: '{group_name}' 匹配到 '{cache_name}', wxid: {wxid}")
                self.groups_cache[group_name] = wxid
                return wxid

        logger.warning(f"未找到群聊 {group_name} 的wxid，所有匹配方法均失败")
        return None

    def find_member_wxid(self, group_wxid, member_name):
        """在指定群聊中查找成员wxid"""
        # 首先检查是否有该群的缓存
        if group_wxid not in self.members_cache:
            logger.info(f"缓存中没有群 {group_wxid} 的成员信息，尝试获取")
            self._update_group_members(group_wxid)

        # 查找群成员
        if group_wxid in self.members_cache:
            # 精确匹配
            if member_name in self.members_cache[group_wxid]:
                logger.info(
                    f"在群 {group_wxid} 成员中找到 {member_name} 的wxid: {self.members_cache[group_wxid][member_name]}")
                return self.members_cache[group_wxid][member_name]

            # 模糊匹配
            logger.info(f"在群 {group_wxid} 成员中未精确匹配到 {member_name}，尝试模糊匹配")
            for cache_name, wxid in self.members_cache[group_wxid].items():
                if (member_name in cache_name) or (cache_name in member_name):
                    logger.info(f"模糊匹配成功: '{member_name}' 匹配到群成员 '{cache_name}', wxid: {wxid}")
                    # 添加到缓存以备将来使用
                    self.members_cache[group_wxid][member_name] = wxid
                    return wxid

            # 如果找不到，可能是缓存过期，尝试刷新
            logger.info(f"缓存中未找到群成员 {member_name}，尝试刷新缓存")
            self._update_group_members(group_wxid)

            # 再次尝试精确匹配
            if member_name in self.members_cache[group_wxid]:
                logger.info(
                    f"刷新缓存后在群 {group_wxid} 成员中找到 {member_name} 的wxid: {self.members_cache[group_wxid][member_name]}")
                return self.members_cache[group_wxid][member_name]

            # 再次尝试模糊匹配
            for cache_name, wxid in self.members_cache[group_wxid].items():
                if (member_name in cache_name) or (cache_name in member_name):
                    logger.info(f"刷新缓存后模糊匹配成功: '{member_name}' 匹配到群成员 '{cache_name}', wxid: {wxid}")
                    self.members_cache[group_wxid][member_name] = wxid
                    return wxid

        # 如果在群成员中找不到，尝试从好友列表中查找
        logger.info(f"在群 {group_wxid} 成员中未找到 {member_name}，尝试从好友列表查找")
        friend_wxid = self.find_friend_wxid(member_name)

        if friend_wxid:
            # 如果在好友列表中找到，添加到群成员缓存中
            if group_wxid in self.members_cache:
                self.members_cache[group_wxid][member_name] = friend_wxid
            return friend_wxid

        logger.warning(f"无法找到成员 {member_name} 的wxid，所有匹配方法均失败")
        return None

    def process_message(self, data):
        """
        处理消息队列中的消息
        
        Args:
            data: 原始消息数据，格式为
                {
                    "receiver_name": ["朱欣园"], 
                    "message": "测试一下33", 
                    "group_name": ["测试发送消息"], 
                    "time": "2025-03-03 11:20:51"
                }
        """
        try:
            receiver_names = data.get("receiver_name", [])
            content = data.get("message", "")
            group_names = data.get("group_name", [])

            # 日志记录完整消息数据用于调试
            logger.info(
                f"处理消息: receiver_names={receiver_names}, group_names={group_names}, content长度={len(content)}")

            # 处理可能的错误情况
            if not receiver_names and not group_names:
                logger.error("既没有指定群聊也没有指定好友，无法发送消息")
                return

            # 判断是否为图片URL消息
            is_image_url = False
            if content.startswith(('http://', 'https://')) and any(
                    content.lower().endswith(ext) for ext in ['.jpg', '.jpeg', '.png', '.gif', '.webp']):
                is_image_url = True
                logger.info(f"检测到图片URL消息: {content}")
            # 如果不是图片扩展名，但URL包含图片相关关键词，也视为图片
            elif content.startswith(('http://', 'https://')) and any(keyword in content.lower() for keyword in
                                                                     ['image', 'photo', 'picture', 'img', 'pic', '.jpg',
                                                                      '.jpeg', '.png', '.gif']):
                is_image_url = True
                logger.info(f"检测到可能的图片URL消息: {content}")

            # 判断是群聊消息还是个人消息
            if group_names and any(group_names):
                # 处理群聊消息
                for group_name in group_names:
                    # 先尝试从映射表获取群聊wxid
                    group_wxid = self.group_mapping.get(group_name)

                    # 如果映射表中没有，再尝试从缓存获取
                    if not group_wxid:
                        group_wxid = self.find_group_wxid(group_name)

                    if not group_wxid:
                        logger.error(f"无法找到群聊 {group_name} 的wxid")
                        continue

                    # 处理@功能
                    at_wxids = []
                    if receiver_names and any(receiver_names) and not is_image_url:
                        # 检查是否@全体成员
                        if "所有人" in receiver_names or "全体成员" in receiver_names or "all" in receiver_names:
                            # @全体成员的特殊处理
                            logger.info(f"检测到@全体成员请求，群聊: {group_name}")
                            at_msg = "@所有人 " + content
                            result = self.send_at_all(group_wxid, at_msg)
                            logger.info(f"发送群聊@全体成员消息结果: {result}, 群聊: {group_name}, 内容: {content}")
                            continue
                        else:
                            # 获取接收者的wxid
                            for receiver_name in receiver_names:
                                member_wxid = self.user_mapping.get(receiver_name)

                                # 如果映射表中没有，再尝试从缓存获取
                                if not member_wxid:
                                    member_wxid = self.find_member_wxid(group_wxid, receiver_name)

                                if member_wxid:
                                    at_wxids.append(member_wxid)
                                else:
                                    logger.warning(f"在群 {group_name} 中未找到成员 {receiver_name} 的wxid")

                            # 如果找到了@的对象
                            if at_wxids:
                                # 构造@消息
                                at_names = []
                                for receiver_name in receiver_names:
                                    if receiver_name not in ["所有人", "全体成员", "all"]:  # 防止特殊标记被当作名字@
                                        at_names.append(f"@{receiver_name}")

                                at_msg = " ".join(at_names) + " " + content
                                # 发送@消息，包含at_list
                                result = self.send_message(group_wxid, at_msg, at_wxids)
                                logger.info(
                                    f"发送群聊@消息结果: {result}, 群聊: {group_name}, @成员: {receiver_names}, 内容: {content}")
                                continue

                    # 如果是图片URL消息
                    if is_image_url:
                        result = self.send_image_url(group_wxid, content)
                        logger.info(f"发送群聊图片消息结果: {result}, 群聊: {group_name}, 图片URL: {content}")
                    else:
                        # 如果没有@或者@失败，发送普通消息
                        result = self.send_message(group_wxid, content)
                        logger.info(f"发送群聊普通消息结果: {result}, 群聊: {group_name}, 内容: {content}")

            # 处理个人消息
            elif receiver_names and any(receiver_names):
                for receiver_name in receiver_names:
                    # 先尝试从映射表获取好友wxid
                    friend_wxid = self.user_mapping.get(receiver_name)

                    # 如果映射表中没有，再尝试从缓存获取
                    if not friend_wxid:
                        friend_wxid = self.find_friend_wxid(receiver_name)

                    if not friend_wxid:
                        logger.error(f"无法找到好友 {receiver_name} 的wxid")
                        continue

                    # 如果是图片URL消息
                    if is_image_url:
                        result = self.send_image_url(friend_wxid, content)
                        logger.info(f"发送个人图片消息结果: {result}, 接收者: {receiver_name}, 图片URL: {content}")
                    else:
                        # 发送个人消息
                        result = self.send_message(friend_wxid, content)
                        logger.info(f"发送个人消息结果: {result}, 接收者: {receiver_name}, 内容: {content}")
            else:
                logger.error("既没有指定群聊也没有指定好友，无法发送消息")

        except Exception as e:
            logger.error(f"处理消息时发生异常: {e}")

    def _start_auto_refresh(self):
        """启动自动刷新缓存的线程"""

        def auto_refresh():
            while True:
                try:
                    # 等待10分钟
                    time.sleep(600)
                    logger.info("定时任务：刷新联系人和群聊缓存")
                    self.refresh_cache()
                except Exception as e:
                    logger.error(f"自动刷新缓存时发生异常: {e}")

        # 创建并启动线程
        refresh_thread = Thread(target=auto_refresh, daemon=True)
        refresh_thread.start()
        logger.info("已启动定时刷新缓存线程，每10分钟刷新一次")

    def send_message(self, to_wxid: str, content: str, at_list=None):
        """
        发送消息，直接调用API的/VXAPI/Msg/SendTxt端点
        
        Args:
            to_wxid: 接收者的wxid
            content: 消息内容
            at_list: 要@的用户wxid列表，可选
        
        Returns:
            响应结果
        """
        try:
            # 处理at_list参数
            at_str = ""
            if at_list:
                if isinstance(at_list, list):
                    at_str = ",".join(at_list)
                elif isinstance(at_list, str):
                    at_str = at_list

            # 确保有有效的微信ID
            if not hasattr(self, 'my_wxid') or not self.my_wxid:
                self.my_wxid = self._get_my_wxid_from_db()
                if not self.my_wxid:
                    # 再次尝试从API获取
                    self._try_get_wxid_from_api()

                if not self.my_wxid:
                    logger.error("无法获取我的微信ID，无法发送消息")
                    return {"success": False, "error": "无法获取我的微信ID"}

            # 构造请求数据
            json_param = {
                "Wxid": self.my_wxid,  # 发送者的wxid
                "ToWxid": to_wxid,  # 接收者的wxid
                "Content": content,  # 消息内容
                "Type": 1,  # 消息类型（1为文本）
                "At": at_str  # 要@的用户
            }

            # 日志记录请求参数，遮盖消息内容
            safe_param = json_param.copy()
            if len(content) > 30:
                safe_param["Content"] = content[:30] + "..."

                # 直接发送HTTP请求到API端点
            url = f'http://{self.api_ip}:{self.api_port}/VXAPI/Msg/SendTxt'
            logger.info(f"发送消息请求: URL={url}, 数据={safe_param}")

            # 发送请求
            max_retries = 3
            retry_count = 0
            success = False
            response = None

            while retry_count < max_retries and not success:
                try:
                    response = requests.post(url, json=json_param, timeout=10)
                    if response.status_code == 200:
                        success = True
                    else:
                        retry_count += 1
                        logger.warning(
                            f"API请求失败，状态码: {response.status_code}，重试次数: {retry_count}/{max_retries}")
                        time.sleep(1)  # 等待1秒后重试
                except Exception as e:
                    retry_count += 1
                    logger.warning(f"API请求异常: {e}，重试次数: {retry_count}/{max_retries}")
                    time.sleep(1)  # 等待1秒后重试

            # 处理响应
            if success and response:
                try:
                    result = response.json()
                    if result.get("Success"):
                        logger.info(f"消息发送成功，响应: {result}")
                        data = result.get("Data")
                        # 返回客户端消息ID、创建时间和新消息ID
                        return {
                            "success": True,
                            "client_msg_id": data.get("List")[0].get("ClientMsgid"),
                            "create_time": data.get("List")[0].get("Createtime"),
                            "new_msg_id": data.get("List")[0].get("NewMsgId")
                        }
                    else:
                        error_msg = result.get("Message", "未知错误")
                        logger.error(f"API返回失败: {error_msg}")

                        # 如果是登录问题，尝试重新获取wxid
                        if "请先登录" in error_msg or "未登录" in error_msg:
                            logger.warning("检测到登录问题，尝试重新获取wxid")
                            self._try_get_wxid_from_api()

                        return {"success": False, "error": error_msg}
                except Exception as e:
                    logger.error(f"处理API响应时出错: {e}, 响应内容: {response.text}")
                    return {"success": False, "error": f"处理响应错误: {str(e)}"}
            else:
                error_msg = f"API请求失败，重试{max_retries}次后依然失败"
                if response:
                    error_msg += f"，状态码: {response.status_code}"
                logger.error(error_msg)
                return {"success": False, "error": error_msg}
        except Exception as e:
            logger.error(f"发送消息时发生异常: {e}")
            return {"success": False, "error": str(e)}

    def send_image_url(self, to_wxid: str, image_url: str):
        """
        发送图片URL消息，先下载图片转为Base64后调用API的/VXAPI/Msg/UploadImg端点
        
        Args:
            to_wxid: 接收者的wxid
            image_url: 图片URL
        
        Returns:
            响应结果
        """
        try:
            # 确保有有效的微信ID
            if not hasattr(self, 'my_wxid') or not self.my_wxid:
                self.my_wxid = self._get_my_wxid_from_db()
                if not self.my_wxid:
                    # 再次尝试从API获取
                    self._try_get_wxid_from_api()

                if not self.my_wxid:
                    logger.error("无法获取我的微信ID，无法发送消息")
                    return {"success": False, "error": "无法获取我的微信ID"}

            # 先下载图片
            logger.info(f"开始下载图片: {image_url}")
            try:
                response = requests.get(image_url, timeout=30)
                if response.status_code != 200:
                    logger.error(f"下载图片失败，状态码: {response.status_code}")
                    return self.send_message(to_wxid, f"[图片下载失败] {image_url}")

                # 将图片内容转换为Base64编码
                image_data = response.content
                base64_data = base64.b64encode(image_data).decode('utf-8')
                logger.info(f"图片下载成功，大小: {len(image_data)} 字节, Base64长度: {len(base64_data)}")

            except Exception as e:
                logger.error(f"下载图片时出错: {e}")
                return self.send_message(to_wxid, f"[图片下载失败] {image_url} - {str(e)}")

            # 构造请求数据
            json_param = {
                "Wxid": self.my_wxid,  # 发送者的wxid
                "ToWxid": to_wxid,  # 接收者的wxid
                "Base64": base64_data  # 图片Base64编码
            }

            # 日志记录请求参数 (不记录Base64数据以避免日志过大)
            logger.info(f"发送图片消息请求参数: {{Wxid: {self.my_wxid}, ToWxid: {to_wxid}, Base64: [数据已省略]}}")

            # 直接发送HTTP请求到API端点
            url = f'http://{self.api_ip}:{self.api_port}/VXAPI/Msg/UploadImg'
            logger.info(f"发送图片消息请求: URL={url}")

            # 发送请求
            max_retries = 3
            retry_count = 0
            success = False
            response = None

            while retry_count < max_retries and not success:
                try:
                    response = requests.post(url, json=json_param, timeout=30)  # 图片可能较大，增加超时时间
                    if response.status_code == 200:
                        success = True
                    else:
                        retry_count += 1
                        logger.warning(
                            f"API请求失败，状态码: {response.status_code}，重试次数: {retry_count}/{max_retries}")
                        time.sleep(1)  # 等待1秒后重试
                except Exception as e:
                    retry_count += 1
                    logger.warning(f"API请求异常: {e}，重试次数: {retry_count}/{max_retries}")
                    time.sleep(1)  # 等待1秒后重试

            # 处理响应
            if success and response:
                try:
                    result = response.json()
                    if result.get("Success"):
                        logger.info(f"图片消息发送成功，响应: {result}")
                        return {
                            "success": True,
                            "data": result.get("Data")
                        }
                    else:
                        error_msg = result.get("Message", "未知错误")
                        logger.error(f"API返回失败: {error_msg}")

                        # 如果图片发送接口失败，尝试使用文本方式发送URL
                        if retry_count >= max_retries - 1:
                            logger.info(f"图片发送失败，尝试使用文本方式发送URL")
                            return self.send_message(to_wxid, f"[图片] {image_url}")

                        return {"success": False, "error": error_msg}
                except Exception as e:
                    logger.error(f"处理API响应时出错: {e}, 响应内容: {response.text}")
                    return {"success": False, "error": f"处理响应错误: {str(e)}"}
            else:
                error_msg = f"API请求失败，重试{max_retries}次后依然失败"
                if response:
                    error_msg += f"，状态码: {response.status_code}"
                logger.error(error_msg)

                # 所有重试都失败，尝试使用文本方式发送URL
                logger.info(f"图片发送失败，尝试使用文本方式发送URL")
                return self.send_message(to_wxid, f"[图片] {image_url}")
        except Exception as e:
            logger.error(f"发送图片消息时发生异常: {e}")
            return {"success": False, "error": str(e)}

    def send_at_all(self, to_wxid: str, content: str):
        """
        发送@全体成员消息，直接调用API的/VXAPI/Msg/SendTxt端点
        
        Args:
            to_wxid: 群聊的wxid
            content: 消息内容
        
        Returns:
            响应结果
        """
        try:
            # 确保有有效的微信ID
            if not hasattr(self, 'my_wxid') or not self.my_wxid:
                self.my_wxid = self._get_my_wxid_from_db()
                if not self.my_wxid:
                    # 再次尝试从API获取
                    self._try_get_wxid_from_api()

                if not self.my_wxid:
                    logger.error("无法获取我的微信ID，无法发送消息")
                    return {"success": False, "error": "无法获取我的微信ID"}

            # 构造请求数据 - @所有人的特殊格式
            json_param = {
                "Wxid": self.my_wxid,  # 发送者的wxid
                "ToWxid": to_wxid,  # 接收者的wxid
                "Content": content,  # 消息内容
                "Type": 1,  # 消息类型（1为文本）
                "At": "notify@all"  # 特殊标记，表示@所有人
            }

            # 日志记录请求参数，遮盖消息内容
            safe_param = json_param.copy()
            if len(content) > 30:
                safe_param["Content"] = content[:30] + "..."

                # 直接发送HTTP请求到API端点
            url = f'http://{self.api_ip}:{self.api_port}/VXAPI/Msg/SendTxt'
            logger.info(f"发送@全体成员消息请求: URL={url}, 数据={safe_param}")

            # 发送请求
            max_retries = 3
            retry_count = 0
            success = False
            response = None

            while retry_count < max_retries and not success:
                try:
                    response = requests.post(url, json=json_param, timeout=10)
                    if response.status_code == 200:
                        success = True
                    else:
                        retry_count += 1
                        logger.warning(
                            f"API请求失败，状态码: {response.status_code}，重试次数: {retry_count}/{max_retries}")
                        time.sleep(1)  # 等待1秒后重试
                except Exception as e:
                    retry_count += 1
                    logger.warning(f"API请求异常: {e}，重试次数: {retry_count}/{max_retries}")
                    time.sleep(1)  # 等待1秒后重试

            # 处理响应
            if success and response:
                try:
                    result = response.json()
                    if result.get("Success"):
                        logger.info(f"@全体成员消息发送成功，响应: {result}")
                        data = result.get("Data")
                        # 返回客户端消息ID、创建时间和新消息ID
                        return {
                            "success": True,
                            "client_msg_id": data.get("List")[0].get(
                                "ClientMsgid") if data and "List" in data else None,
                            "create_time": data.get("List")[0].get("Createtime") if data and "List" in data else None,
                            "new_msg_id": data.get("List")[0].get("NewMsgId") if data and "List" in data else None
                        }
                    else:
                        error_msg = result.get("Message", "未知错误")
                        logger.error(f"API返回失败: {error_msg}")

                        # 如果是登录问题，尝试重新获取wxid
                        if "请先登录" in error_msg or "未登录" in error_msg:
                            logger.warning("检测到登录问题，尝试重新获取wxid")
                            self._try_get_wxid_from_api()

                        return {"success": False, "error": error_msg}
                except Exception as e:
                    logger.error(f"处理API响应时出错: {e}, 响应内容: {response.text}")
                    return {"success": False, "error": f"处理响应错误: {str(e)}"}
            else:
                error_msg = f"API请求失败，重试{max_retries}次后依然失败"
                if response:
                    error_msg += f"，状态码: {response.status_code}"
                logger.error(error_msg)
                return {"success": False, "error": error_msg}
        except Exception as e:
            logger.error(f"发送@全体成员消息时发生异常: {e}")
            return {"success": False, "error": str(e)}


class MessageConsumer(Thread):
    """消息队列消费者，负责从MQ接收消息并处理"""

    def __init__(self, wx_adapter, host='localhost', port=5672, queue='message_queue',
                 username='guest', password='guest'):
        """
        初始化消息消费者
        
        Args:
            wx_adapter: 微信适配器实例
            host: RabbitMQ服务器地址
            port: RabbitMQ服务器端口
            queue: 队列名称
            username: RabbitMQ用户名
            password: RabbitMQ密码
        """
        super().__init__()
        self.wx_adapter = wx_adapter
        self.host = host
        self.port = port
        self.queue = queue
        self.username = username
        self.password = password
        self.connection = None
        self.channel = None
        self.running = True
        self.daemon = True  # 设置为守护线程，主线程结束时自动结束
        logger.info(f"初始化 MessageConsumer: host={self.host}, port={self.port}, queue={self.queue}")

    def connect(self):
        """连接到RabbitMQ服务器"""
        try:
            logger.info(f"正在连接 RabbitMQ: {self.host}:{self.port}, 队列: {self.queue}")
            # 创建连接凭证
            credentials = pika.PlainCredentials(self.username, self.password)
            # 创建连接参数
            parameters = pika.ConnectionParameters(
                host=self.host,
                port=self.port,
                credentials=credentials,
                heartbeat=600,
                blocked_connection_timeout=300
            )
            # 建立连接
            self.connection = pika.BlockingConnection(parameters)
            # 创建频道
            self.channel = self.connection.channel()
            # 声明队列
            self.channel.queue_declare(queue=self.queue, durable=True)
            logger.info(f"成功连接到 RabbitMQ 服务器 {self.host}:{self.port}, 队列: {self.queue}")
            return True
        except Exception as e:
            logger.error(f"连接 RabbitMQ 失败: {e}, host={self.host}, port={self.port}, queue={self.queue}")
            return False

    def run(self):
        """启动消费者线程"""
        while self.running:
            try:
                if not self.connection or self.connection.is_closed:
                    if not self.connect():
                        time.sleep(5)  # 连接失败，等待5秒后重试
                        continue

                # 设置每次只处理一条消息
                self.channel.basic_qos(prefetch_count=1)

                # 定义消息处理回调函数
                def on_message(ch, method, properties, body):
                    try:
                        # 解码消息内容
                        message = body.decode('utf-8')
                        logger.info(f"收到消息: {message}")

                        # 解析消息内容
                        data_list = json.loads(message)

                        # 处理每条消息
                        for data in data_list:
                            # 直接处理消息
                            self.wx_adapter.process_message(data)

                        # 确认消息已处理
                        ch.basic_ack(delivery_tag=method.delivery_tag)
                    except json.JSONDecodeError as e:
                        logger.error(f"解析消息内容失败: {e}")
                        # 消息格式错误，直接确认丢弃
                        ch.basic_ack(delivery_tag=method.delivery_tag)
                    except Exception as e:
                        logger.error(f"处理消息时发生异常: {e}")
                        # 消息处理失败，重新入队
                        ch.basic_nack(delivery_tag=method.delivery_tag, requeue=True)

                # 开始消费消息
                self.channel.basic_consume(queue=self.queue, on_message_callback=on_message)

                logger.info(f"开始从队列 {self.queue} 消费消息...")

                # 使用更健壮的方式启动消费
                try:
                    self.channel.start_consuming()
                except Exception as e:
                    if not self.running:
                        # 如果是因为调用了 stop 方法导致的异常，则忽略
                        logger.info("消息消费已停止")
                    else:
                        # 其他异常则记录并重试
                        logger.error(f"消费消息时发生异常: {e}")
                        if self.connection and not self.connection.is_closed:
                            try:
                                self.connection.close()
                            except:
                                pass
                        self.connection = None
                        time.sleep(5)  # 发生异常，等待5秒后重试

            except pika.exceptions.AMQPConnectionError as e:
                logger.error(f"RabbitMQ 连接断开: {e}")
                self.connection = None
                time.sleep(5)  # 连接断开，等待5秒后重试

    def stop(self):
        """停止消费者线程"""
        self.running = False
        try:
            if self.channel and hasattr(self.channel, 'stop_consuming'):
                try:
                    self.channel.stop_consuming()
                except Exception as e:
                    logger.warning(f"停止消费时发生异常: {e}")

            if self.connection and not self.connection.is_closed:
                try:
                    self.connection.close()
                except Exception as e:
                    logger.warning(f"关闭连接时发生异常: {e}")

            logger.info("RabbitMQ 消费者已停止")
        except Exception as e:
            logger.error(f"停止 RabbitMQ 消费者时发生异常: {e}")


class MQListener:
    """消息队列监听器，用于启动和管理消息消费者"""

    def __init__(self, config):
        """
        初始化消息队列监听器
        
        Args:
            config: 配置字典，包含以下键:
                - rabbitmq_host: RabbitMQ服务器地址
                - rabbitmq_port: RabbitMQ服务器端口
                - rabbitmq_queue: 队列名称
                - rabbitmq_user: RabbitMQ用户名
                - rabbitmq_password: RabbitMQ密码
        """
        self.config = config

        # 初始化微信适配器
        self.wx_adapter = WXAdapter()

        # 初始化消息消费者
        self.message_consumer = MessageConsumer(
            wx_adapter=self.wx_adapter,
            host=config.get('rabbitmq_host', 'localhost'),
            port=config.get('rabbitmq_port', 5672),
            queue=config.get('rabbitmq_queue', 'message_queue'),
            username=config.get('rabbitmq_user', 'guest'),
            password=config.get('rabbitmq_password', 'guest')
        )

    def start(self):
        """启动消息队列监听器"""
        logger.info("启动消息队列监听器")
        self.message_consumer.start()

    def stop(self):
        """停止消息队列监听器"""
        logger.info("停止消息队列监听器")
        self.message_consumer.stop()


# 使用示例
if __name__ == "__main__":
    # 读取项目配置
    main_config = load_config()
    mq_config = main_config.get("MessageQueue", {})

    # 添加命令行参数支持
    parser = argparse.ArgumentParser(description="消息队列监听器")
    parser.add_argument('--test', action='store_true', help='启用测试模式')
    parser.add_argument('--list', action='store_true', help='列出已缓存的群聊和用户')
    parser.add_argument('--group', type=str, help='测试发送的群名称')
    parser.add_argument('--user', type=str, help='测试发送的用户名称')
    parser.add_argument('--message', type=str, help='测试发送的消息内容')
    parser.add_argument('--at', type=str, help='需要@的用户，多个用户用逗号分隔')
    parser.add_argument('--atall', action='store_true', help='@全体成员')
    parser.add_argument('--image', type=str, help='测试发送的图片URL')
    args = parser.parse_args()

    # 测试模式
    if args.test or args.list:
        logger.info("启动测试模式")
        wx_adapter = WXAdapter()

        # 列出已缓存的群聊和用户
        if args.list:
            logger.info("刷新缓存并显示可用的群和用户")
            wx_adapter.refresh_cache()
            logger.info(f"可用的群列表: {list(wx_adapter.groups_cache.keys())}")
            logger.info(f"可用的好友列表: {list(wx_adapter.friends_cache.keys())}")
            exit(0)

        # 测试消息内容：优先使用图片URL，其次使用普通消息
        test_message = ""
        is_image = False

        if args.image:
            test_message = args.image
            is_image = True
            logger.info(f"测试发送图片URL: {test_message}")
        else:
            test_message = args.message or "这是一条测试消息，来自MQ监听器 v1.8"
            logger.info(f"测试发送文本消息: {test_message}")

        # 测试发送到群
        if args.group:
            logger.info(f"测试发送群消息到: {args.group}")

            # 处理@用户
            at_users = []
            if args.atall and not is_image:  # @全体成员
                at_users = ["所有人"]
                logger.info(f"需要@全体成员")
            elif args.at and not is_image:  # @指定用户
                at_users = args.at.split(',')
                logger.info(f"需要@的用户: {at_users}")

            data = {
                "group_name": [args.group],
                "message": test_message,
                "receiver_name": at_users,
                "time": time.strftime("%Y-%m-%d %H:%M:%S")
            }
            wx_adapter.process_message(data)

        # 测试发送到用户
        if args.user:
            logger.info(f"测试发送个人消息到: {args.user}")
            data = {
                "group_name": [],
                "message": test_message,
                "receiver_name": [args.user],
                "time": time.strftime("%Y-%m-%d %H:%M:%S")
            }
            wx_adapter.process_message(data)

        # 如果没有指定群或用户，显示可用的群和用户
        if not args.group and not args.user:
            logger.info("未指定接收者，刷新缓存并显示可用的群和用户")
            wx_adapter.refresh_cache()
            logger.info(f"可用的群列表: {list(wx_adapter.groups_cache.keys())}")
            logger.info(f"可用的好友列表: {list(wx_adapter.friends_cache.keys())}")

            # 测试@功能
            test_group = next(iter(wx_adapter.groups_cache.keys()), None)
            if test_group:
                logger.info(f"测试@功能，发送到群: {test_group}")
                data = {
                    "group_name": [test_group],
                    "message": "这是一条@测试消息",
                    "receiver_name": ["所有人"],
                    "time": time.strftime("%Y-%m-%d %H:%M:%S")
                }
                wx_adapter.process_message(data)

        logger.info("测试完成")
        exit(0)

    # 检查MQ功能是否启用
    if not mq_config.get("enabled", False):
        logger.warning("MessageQueue功能在main_config.toml中未启用，请设置MessageQueue.enabled = true")
        # 尝试读取单独的配置文件
        try:
            with open('config.json', 'r', encoding='utf-8') as f:
                config = json.load(f)
                logger.info("使用config.json中的配置")
        except Exception as e:
            logger.error(f"读取config.json失败: {e}，将使用默认配置")
            config = {
                'rabbitmq_host': 'localhost',
                'rabbitmq_port': 5672,
                'rabbitmq_queue': 'message_queue',
                'rabbitmq_user': 'guest',
                'rabbitmq_password': 'guest'
            }
    else:
        # 使用main_config.toml中的配置
        config = {
            'rabbitmq_host': mq_config.get("host", "localhost"),
            'rabbitmq_port': mq_config.get("port", 5672),
            'rabbitmq_queue': mq_config.get("queue", "message_queue"),
            'rabbitmq_user': mq_config.get("username", "guest"),
            'rabbitmq_password': mq_config.get("password", "guest")
        }
        logger.info(
            f"使用main_config.toml中的配置: {config['rabbitmq_host']}:{config['rabbitmq_port']}, 队列: {config['rabbitmq_queue']}")

    # 启动消息队列监听器
    mq_listener = MQListener(config)
    mq_listener.start()

    # 保持主线程运行
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("接收到键盘中断，停止监听")
        mq_listener.stop()
