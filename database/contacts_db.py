import os
import json
import time
import sqlite3
from datetime import datetime
from loguru import logger
from threading import Lock

# 数据库文件路径
DB_PATH = os.path.join("database", "contacts.db")

class DBConnectionPool:
    """简单的SQLite连接池实现"""
    def __init__(self, db_path, max_connections=5):
        self.db_path = db_path
        self.max_connections = max_connections
        self.connections = []
        self.lock = Lock()
        self.ensure_db_dir()
        
    def ensure_db_dir(self):
        """确保数据库目录存在"""
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        
    def get_connection(self):
        """获取一个数据库连接"""
        with self.lock:
            if self.connections:
                return self.connections.pop()
            else:
                # 创建新连接
                conn = sqlite3.connect(self.db_path, check_same_thread=False)
                return conn
                
    def release_connection(self, conn):
        """释放一个数据库连接回池中"""
        with self.lock:
            if len(self.connections) < self.max_connections:
                self.connections.append(conn)
            else:
                conn.close()
                
    def close_all(self):
        """关闭所有连接"""
        with self.lock:
            for conn in self.connections:
                conn.close()
            self.connections = []

# 创建连接池实例
db_pool = DBConnectionPool(DB_PATH)

# 修改现有函数使用连接池
def get_contacts_from_db(offset=None, limit=None):
    """从数据库获取联系人，支持分页"""
    conn = None
    try:
        conn = db_pool.get_connection()
        cursor = conn.cursor()
        
        # 构建查询语句，支持分页
        query = "SELECT * FROM contacts"
        params = []

        # 添加排序
        query += " ORDER BY nickname COLLATE NOCASE"

        # 添加分页参数
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)

            if offset is not None:
                query += " OFFSET ?"
                params.append(offset)

        # 执行查询
        cursor.execute(query, params)
        rows = cursor.fetchall()

        contacts = []
        for row in rows:
            contact = {
                "wxid": row[0],
                "nickname": row[1],
                "remark": row[2],
                "avatar": row[3],
                "alias": row[4],
                "type": row[5],
                "region": row[6],
                "last_updated": row[7]
            }

            # 解析额外数据
            if row[8]:
                try:
                    extra_data = json.loads(row[8])
                    contact.update(extra_data)
                except:
                    pass

            contacts.append(contact)

        conn.close()

        # 记录日志，区分是否分页
        if offset is not None or limit is not None:
            logger.info(f"从数据库加载了 {len(contacts)} 个联系人 (offset={offset}, limit={limit})")
        else:
            logger.info(f"从数据库加载了所有 {len(contacts)} 个联系人")

        db_pool.release_connection(conn)
        return contacts
    except Exception as e:
        logger.error(f"从数据库获取联系人失败: {str(e)}")
        if conn:
            db_pool.release_connection(conn)
        return []

def save_contacts_to_db(contacts):
    """保存联系人列表到数据库"""
    ensure_db_dir()
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        # 创建表（如果不存在）
        create_contacts_table()

        # 准备批量插入
        current_time = int(time.time())
        for contact in contacts:
            # 提取基本字段
            wxid = contact.get("wxid", "")
            nickname = contact.get("nickname", "")
            remark = contact.get("remark", "")
            avatar = contact.get("avatar", "")
            alias = contact.get("alias", "")

            # 确定联系人类型
            contact_type = contact.get("type", "")
            if not contact_type:
                if wxid.endswith("@chatroom"):
                    contact_type = "group"
                elif wxid.startswith("gh_"):
                    contact_type = "official"
                else:
                    contact_type = "friend"

            # 其他字段
            region = contact.get("region", "")

            # 将其他字段存储为JSON
            extra_data = {}
            for key, value in contact.items():
                if key not in ["wxid", "nickname", "remark", "avatar", "alias", "type", "region"]:
                    extra_data[key] = value

            extra_data_json = json.dumps(extra_data, ensure_ascii=False)

            # 插入或更新联系人
            cursor.execute('''
            INSERT OR REPLACE INTO contacts
            (wxid, nickname, remark, avatar, alias, type, region, last_updated, extra_data)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                wxid,
                nickname,
                remark,
                avatar,
                alias,
                contact_type,
                region,
                current_time,
                extra_data_json
            ))

        conn.commit()
        conn.close()
        logger.success(f"成功保存 {len(contacts)} 个联系人到数据库")
        return True
    except Exception as e:
        logger.error(f"保存联系人到数据库失败: {str(e)}")
        return False

def update_contact_in_db(contact):
    """更新单个联系人信息"""
    ensure_db_dir()
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        # 创建表（如果不存在）
        create_contacts_table()

        # 提取基本字段
        wxid = contact.get("wxid", "")
        if not wxid:
            logger.error("更新联系人失败: 缺少wxid")
            return False

        nickname = contact.get("nickname", "")
        remark = contact.get("remark", "")
        avatar = contact.get("avatar", "")
        alias = contact.get("alias", "")

        # 确定联系人类型
        contact_type = contact.get("type", "")
        if not contact_type:
            if wxid.endswith("@chatroom"):
                contact_type = "group"
            elif wxid.startswith("gh_"):
                contact_type = "official"
            else:
                contact_type = "friend"

        # 其他字段
        region = contact.get("region", "")
        current_time = int(time.time())

        # 将其他字段存储为JSON
        extra_data = {}
        for key, value in contact.items():
            if key not in ["wxid", "nickname", "remark", "avatar", "alias", "type", "region"]:
                extra_data[key] = value

        extra_data_json = json.dumps(extra_data, ensure_ascii=False)

        # 检查联系人是否存在
        cursor.execute("SELECT wxid FROM contacts WHERE wxid = ?", (wxid,))
        exists = cursor.fetchone()

        if exists:
            # 更新现有联系人
            cursor.execute('''
            UPDATE contacts SET
                nickname = ?,
                remark = ?,
                avatar = ?,
                alias = ?,
                type = ?,
                region = ?,
                last_updated = ?,
                extra_data = ?
            WHERE wxid = ?
            ''', (
                nickname,
                remark,
                avatar,
                alias,
                contact_type,
                region,
                current_time,
                extra_data_json,
                wxid
            ))
            logger.debug(f"更新联系人: {wxid}")
        else:
            # 插入新联系人
            cursor.execute('''
            INSERT INTO contacts
            (wxid, nickname, remark, avatar, alias, type, region, last_updated, extra_data)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                wxid,
                nickname,
                remark,
                avatar,
                alias,
                contact_type,
                region,
                current_time,
                extra_data_json
            ))
            logger.debug(f"新增联系人: {wxid}")

        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"更新联系人 {contact.get('wxid', 'unknown')} 失败: {str(e)}")
        return False

def get_contact_from_db(wxid):
    """从数据库获取单个联系人信息"""
    ensure_db_dir()
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        # 查询联系人
        cursor.execute("SELECT * FROM contacts WHERE wxid = ?", (wxid,))
        row = cursor.fetchone()

        if row:
            # 解析联系人数据
            contact = {
                "wxid": row[0],
                "nickname": row[1],
                "remark": row[2],
                "avatar": row[3],
                "alias": row[4],
                "type": row[5],
                "region": row[6],
                "last_updated": row[7]
            }

            # 解析额外数据
            if row[8]:
                try:
                    extra_data = json.loads(row[8])
                    contact.update(extra_data)
                except:
                    pass

            conn.close()
            return contact
        else:
            conn.close()
            return None
    except Exception as e:
        logger.error(f"从数据库获取联系人 {wxid} 失败: {str(e)}")
        return None

def delete_contact_from_db(wxid):
    """从数据库删除联系人"""
    ensure_db_dir()
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        # 删除联系人
        cursor.execute("DELETE FROM contacts WHERE wxid = ?", (wxid,))

        conn.commit()
        conn.close()
        logger.info(f"从数据库删除联系人: {wxid}")
        return True
    except Exception as e:
        logger.error(f"从数据库删除联系人 {wxid} 失败: {str(e)}")
        return False

def get_contacts_count():
    """获取数据库中联系人数量"""
    ensure_db_dir()
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        cursor.execute("SELECT COUNT(*) FROM contacts")
        count = cursor.fetchone()[0]

        conn.close()
        return count
    except Exception as e:
        logger.error(f"获取联系人数量失败: {str(e)}")
        return 0

def get_all_contacts():
    """获取数据库中所有联系人

    Returns:
        联系人列表
    """
    # 直接调用不带分页参数的get_contacts_from_db函数
    return get_contacts_from_db()

# 在init_db函数之前添加以下函数

def ensure_db_dir():
    """确保数据库目录存在"""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

def create_contacts_table():
    """创建联系人表"""
    ensure_db_dir()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # 创建联系人表
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS contacts (
        wxid TEXT PRIMARY KEY,
        nickname TEXT,
        remark TEXT,
        avatar TEXT,
        alias TEXT,
        type TEXT,
        region TEXT,
        last_updated INTEGER,
        extra_data TEXT
    )
    ''')

    # 创建索引以加快查询速度
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_nickname ON contacts (nickname)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_remark ON contacts (remark)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_type ON contacts (type)')

    conn.commit()
    conn.close()
    logger.info("联系人数据表创建完成")

# 初始化数据库
def init_db():
    """初始化数据库"""
    create_contacts_table()
    logger.info("联系人数据库初始化完成")

# 内存缓存
_contacts_cache = {}

def clear_contacts_cache():
    """清除联系人缓存"""
    global _contacts_cache
    _contacts_cache = {}
    logger.info("联系人缓存已清除")

# 当模块被导入时自动初始化数据库
init_db()
