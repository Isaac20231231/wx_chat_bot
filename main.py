import asyncio
import os
import sys
import time
import tomllib
import traceback
import threading
import subprocess
import signal
from pathlib import Path

from loguru import logger
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

# 修改导入语句，确保导入正确的bot_core模块
try:
    # 先尝试使用相对导入（当前目录）
    from .bot_core import bot_core
except ImportError:
    # 如果相对导入失败，尝试使用绝对导入（当前目录）
    import sys
    import os
    current_dir = os.path.dirname(os.path.abspath(__file__))
    if current_dir not in sys.path:
        sys.path.append(current_dir)
    from bot_core import bot_core, set_bot_instance, update_bot_status

# 管理后台启动函数
def start_admin_server(config):
    """启动管理后台服务器"""
    try:
        # 导入需要的模块
        from admin.server import start_server

        admin_config = config.get("Admin", {})
        admin_enabled = admin_config.get("enabled", True)

        if not admin_enabled:
            logger.info("管理后台功能已禁用")
            return None

        admin_host = admin_config.get("host", "0.0.0.0")
        admin_port = admin_config.get("port", 9090)
        admin_username = admin_config.get("username", "admin")
        admin_password = admin_config.get("password", "admin123")
        admin_debug = admin_config.get("debug", False)

        # 提前启动管理后台服务
        logger.info(f"启动管理后台，地址: {admin_host}:{admin_port}")
        logger.info(f"管理员账号: {admin_username}, 密码从配置文件读取")

        # 标记当前正在启动的管理后台实例
        admin_status_file = Path("admin/admin_server_status.txt")
        with open(admin_status_file, "w") as f:
            f.write(f"启动时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"主机: {admin_host}:{admin_port}\n")
            f.write(f"状态: 正在启动\n")

        server_thread = start_server(
            host_arg=admin_host,
            port_arg=admin_port,
            username_arg=admin_username,
            password_arg=admin_password,
            debug_arg=admin_debug,
            bot=None  # 暂时没有bot实例
        )

        # 更新状态文件
        with open(admin_status_file, "w") as f:
            f.write(f"启动时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"主机: {admin_host}:{admin_port}\n")
            f.write(f"状态: 运行中\n")

        logger.success(f"管理后台已启动: http://{admin_host}:{admin_port}")
        return server_thread
    except Exception as e:
        logger.error(f"启动管理后台时出错: {e}")
        logger.error(traceback.format_exc())
        return None

# 管理后台启动函数
def start_service_server(config):
    """启动管理后台服务器"""
    try:
        # 导入需要的模块
        from api_service.server import start_server

        api_service_config = config.get("ApiService", {})
        api_service_enabled = api_service_config.get("enabled", True)

        if not api_service_enabled:
            logger.info("ApiService功能已禁用")
            return None

        admin_host = api_service_config.get("host", "0.0.0.0")
        admin_port = api_service_config.get("port", 18888)
        admin_debug = api_service_config.get("debug", False)

        # 提前启动管理后台服务
        logger.info(f"启动ApiService功能，地址: {admin_host}:{admin_port}")

        # 标记当前正在启动的管理后台实例
        admin_status_file = Path("api_service/api_service_status.txt")
        with open(admin_status_file, "w") as f:
            f.write(f"启动时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"主机: {admin_host}:{admin_port}\n")
            f.write(f"状态: 正在启动\n")

        server_thread = start_server(
            host_arg=admin_host,
            port_arg=admin_port,
            debug_arg=admin_debug,
            bot=None  # 暂时没有bot实例
        )

        # 更新状态文件
        with open(admin_status_file, "w") as f:
            f.write(f"启动时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"主机: {admin_host}:{admin_port}\n")
            f.write(f"状态: 运行中\n")

        logger.success(f"管理后台已启动: http://{admin_host}:{admin_port}")
        return server_thread
    except Exception as e:
        logger.error(f"启动管理后台时出错: {e}")
        logger.error(traceback.format_exc())
        return None

# 消息队列监听服务启动函数
def start_mq_listener(config):
    """
    启动 mq_listener.py 消息队列监听服务
    
    Args:
        config: 主配置文件，包含MessageQueue配置
        
    Returns:
        subprocess.Popen对象：启动的进程
    """
    try:
        global mq_listener_process
        
        # 从配置中获取MessageQueue配置
        mq_config = config.get("MessageQueue", {})
        mq_enabled = mq_config.get("enabled", True)
        
        if not mq_enabled:
            logger.info("消息队列监听功能已禁用")
            return None
        
        # 检查文件是否存在
        mq_listener_path = Path("mq_listener.py")
        if not mq_listener_path.exists():
            logger.error(f"消息队列监听文件不存在: {mq_listener_path}")
            return None
        
        logger.info("正在启动消息队列监听服务...")
        
        # 启动子进程，使用当前Python解释器
        mq_listener_process = subprocess.Popen(
            [sys.executable, str(mq_listener_path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=1,
            universal_newlines=True  # 使用文本模式处理输出
        )
        
        # 日志处理改为直接使用logger对象进行记录，不使用额外函数和级别区分
        def log_reader():
            # 合并stdout和stderr的输出，统一用info级别记录
            import itertools
            for line in itertools.chain(mq_listener_process.stdout, mq_listener_process.stderr):
                if line.strip():
                    logger.info(line.strip())
        
        # 只启动一个线程处理所有输出
        threading.Thread(
            target=log_reader,
            daemon=True
        ).start()
        
        logger.success(f"消息队列监听服务已启动，进程ID: {mq_listener_process.pid}")
        return mq_listener_process
    except Exception as e:
        logger.error(f"启动消息队列监听服务时出错: {e}")
        logger.error(traceback.format_exc())
        return None

def stop_mq_listener():
    """停止消息队列监听服务"""
    global mq_listener_process
    
    if mq_listener_process is not None:
        try:
            if mq_listener_process.poll() is None:  # 检查进程是否仍在运行
                logger.info("正在关闭消息队列监听服务...")
                # 尝试正常终止进程
                mq_listener_process.terminate()
                
                # 等待进程结束，最多等待5秒
                try:
                    mq_listener_process.wait(timeout=5)
                    logger.success("消息队列监听服务已正常关闭")
                except subprocess.TimeoutExpired:
                    # 如果超时，强制终止进程
                    mq_listener_process.kill()
                    logger.warning("消息队列监听服务已强制终止")
        except Exception as e:
            logger.error(f"关闭消息队列监听服务时出错: {e}")


def is_api_message(record):
    return record["level"].name == "API"


class ConfigChangeHandler(FileSystemEventHandler):
    def __init__(self, restart_callback):
        self.restart_callback = restart_callback
        self.last_triggered = 0
        self.cooldown = 2  # 冷却时间(秒)
        self.waiting_for_change = False  # 是否在等待文件改变

    def on_modified(self, event):
        if not event.is_directory:
            current_time = time.time()
            if current_time - self.last_triggered < self.cooldown:
                return

            file_path = Path(event.src_path).resolve()
            if (file_path.name == "main_config.toml" or
                    "plugins" in str(file_path) and file_path.suffix in ['.py', '.toml']):
                logger.info(f"检测到文件变化: {file_path}")
                self.last_triggered = current_time
                if self.waiting_for_change:
                    logger.info("检测到文件改变，正在重启...")
                    self.waiting_for_change = False
                self.restart_callback()


async def main():
    # 设置工作目录为脚本所在目录
    script_dir = Path(__file__).resolve().parent
    os.chdir(script_dir)

    # 更新初始化状态
    update_bot_status("initializing", "系统初始化中")

    # 读取配置文件
    config_path = script_dir / "main_config.toml"
    try:
        with open(config_path, "rb") as f:
            config = tomllib.load(f)
        logger.success("读取主设置成功")
    except Exception as e:
        logger.error(f"读取主设置失败: {e}")
        return

    # 启动管理后台（提前启动）
    admin_server_thread = start_admin_server(config)

    # 启动serviceApi服务
    service_server_thread = start_service_server(config)
    
    # 启动消息队列监听服务
    mq_listener_process = start_mq_listener(config)

    # 启动 linuxService - 现在已经在entrypoint.sh中启动
    # try:
    #     linux_service_path = Path("849/pad/linuxService")
    #     if linux_service_path.exists():
    #         logger.info("正在启动 linuxService...")
    #         # 使用subprocess在后台启动linuxService
    #         # 检测当前操作系统
    #         import platform
    #         start_options = {
    #             'shell': True,
    #             'stdout': subprocess.PIPE,
    #             'stderr': subprocess.PIPE
    #         }
    #
    #         # 如果是Windows系统，添加Windows特有的标志
    #         if platform.system() == 'Windows':
    #             start_options['creationflags'] = 0x08000000  # CREATE_NO_WINDOW
    #
    #         # 启动服务
    #         service_process = subprocess.Popen(
    #             str(linux_service_path),
    #             **start_options
    #         )
    #
    #         # 将进程对象存储在全局变量中，以便在程序退出时关闭
    #         global linux_service_process
    #         linux_service_process = service_process
    #         logger.success("linuxService 启动成功，进程ID: {}", service_process.pid)
    #     else:
    #         logger.warning("linuxService 文件不存在: {}", linux_service_path)
    # except Exception as e:
    #     logger.error("linuxService 启动失败: {}", e)
    #     logger.error(traceback.format_exc())
    logger.info("linuxService 已由 entrypoint.sh 启动")

    # 检查是否启用自动重启
    auto_restart = config.get("XYBot", {}).get("auto-restart", False)

    if auto_restart:
        # 设置监控
        observer = Observer()
        plugins_path = script_dir / "plugins"

        handler = ConfigChangeHandler(None)

        def restart_program():
            logger.info("正在重启程序...")
            # 清理资源
            observer.stop()
            # 停止消息队列监听服务
            stop_mq_listener()
            try:
                import multiprocessing.resource_tracker
                multiprocessing.resource_tracker._resource_tracker.clear()
            except Exception as e:
                logger.warning(f"清理资源时出错: {e}")
            # 重启程序
            os.execv(sys.executable, [sys.executable] + sys.argv)

        handler.restart_callback = restart_program
        observer.schedule(handler, str(config_path.parent), recursive=False)
        observer.schedule(handler, str(plugins_path), recursive=True)
        observer.start()

        try:
            # 运行机器人核心
            bot = await bot_core()

            # 保持程序运行
            while True:
                await asyncio.sleep(1)

        except KeyboardInterrupt:
            logger.info("收到终止信号，正在关闭...")
            # 先停止监控
            observer.stop()
            observer.join()
            # 停止消息队列监听服务
            stop_mq_listener()
            # 调用清理函数
            cleanup()
        except Exception as e:
            logger.error(f"程序发生错误: {e}")
            logger.error(traceback.format_exc())
            logger.info("等待文件改变后自动重启...")
            handler.waiting_for_change = True

            while handler.waiting_for_change:
                await asyncio.sleep(1)
    else:
        # 直接运行主程序，不启用监控
        try:
            # 运行机器人核心
            bot = await bot_core()

            # 保持程序运行
            while True:
                await asyncio.sleep(1)

        except KeyboardInterrupt:
            logger.info("收到终止信号，正在关闭...")
            # 停止消息队列监听服务
            stop_mq_listener()
        except Exception as e:
            logger.error(f"发生错误: {e}")
            logger.error(traceback.format_exc())
            # 停止消息队列监听服务
            stop_mq_listener()
            # 调用清理函数
            cleanup()


# 定义全局变量来存储linuxService进程
# 在程序退出时关闭该进程
linux_service_process = None
mq_listener_process = None

# 清理函数，在程序退出时调用
def cleanup():
    global linux_service_process, mq_listener_process
    
    # 关闭消息队列监听服务
    if mq_listener_process is not None:
        stop_mq_listener()
    
    # 关闭 linuxService 进程
    if linux_service_process is not None:
        try:
            logger.info("正在关闭 linuxService 进程...")
            linux_service_process.terminate()
            # 等待进程结束，最多等待5秒
            linux_service_process.wait(timeout=5)
            logger.success("linuxService 进程已关闭")
        except Exception as e:
            logger.error("linuxService 进程关闭失败: {}", e)
            # 如果正常终止失败，尝试强制终止
            try:
                linux_service_process.kill()
                logger.warning("linuxService 进程已强制终止")
            except Exception as e2:
                logger.error("强制终止 linuxService 进程失败: {}", e2)

if __name__ == "__main__":
    # 防止低版本Python运行
    if sys.version_info.major != 3 and sys.version_info.minor != 11:
        print("请使用Python3.11")
        sys.exit(1)
    print(
        "  __   __ __   __ __   __ ____   _____  _______ \n"
        "  \ \ / / \ \ / / \ \ / /|  _ \ / _ \ \|__   __|\n"
        "   \ V /   \ V /   \ V / | |_) | | | | |  | |   \n"
        "    > <     > <     > <  |  _ <| | | | |  | |   \n"
        "   / . \   / . \   / . \ | |_) | |_| | |  | |   \n"
        "  /_/ \_\ /_/ \_\ /_/ \_\|____/ \___/|_|  |_|   \n"
    )


    logger.remove()

    logger.level("API", no=1, color="<cyan>")

    logger.add(
        "logs/XYBot_{time}.log",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {message}",
        encoding="utf-8",
        enqueue=True,
        retention="2 weeks",
        rotation="00:01",
        backtrace=True,
        diagnose=True,
        level="DEBUG",
    )
    logger.add(
        sys.stdout,
        colorize=True,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level}</level> | {message}",
        level="TRACE",
        enqueue=True,
        backtrace=True,
        diagnose=True,
    )

    # 设置信号处理程序，确保在程序被终止时能够清理资源
    def signal_handler(sig, frame):
        logger.info(f"收到信号 {sig}，正在优雅退出...")
        stop_mq_listener()
        cleanup()
        sys.exit(0)

    # 注册信号处理程序
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("程序被用户中断")
    finally:
        # 确保在退出前清理资源
        stop_mq_listener()
        cleanup()