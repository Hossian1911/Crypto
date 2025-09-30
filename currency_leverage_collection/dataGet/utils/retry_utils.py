"""
重试工具
解决API访问频限问题
"""

import time
import functools
import random

def retry_on_limit(max_retries=None, sleep_time=60):
    """
    通用重试装饰器（遇到频限错误自动 sleep & 重试）
    
    :param max_retries: int|None, 最大重试次数，None表示无限重试
    :param sleep_time: int, 重试前等待时间（秒），默认60
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            retry_count = 0
            
            while True:
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    msg = str(e)
                    
                    # 判断是否是频限相关错误
                    limit_keywords = [
                        "每分钟最多访问", "频次", "频率", "访问过于频繁",
                        "rate limit", "too many requests", "quota exceeded",
                        "访问次数超限", "请求过于频繁", "API调用频率超限"
                    ]
                    
                    is_limit_error = any(keyword in msg for keyword in limit_keywords)
                    
                    if is_limit_error:
                        retry_count += 1
                        
                        # 检查是否超过最大重试次数
                        if max_retries is not None and retry_count > max_retries:
                            print(f"[{func.__name__}] 重试次数超过限制({max_retries})，停止重试")
                            raise e
                        
                        # 增加随机延迟，避免同时重试
                        actual_sleep = sleep_time + random.uniform(0, 30)
                        print(f"[{func.__name__}] 访问频次限制，第{retry_count}次重试，等待{actual_sleep:.1f}秒...")
                        time.sleep(actual_sleep)
                        continue  # 重试
                    else:
                        # 非频限异常，直接抛出
                        print(f"[{func.__name__}] 非频限错误: {msg}")
                        raise e
        
        return wrapper
    return decorator

def retry_on_network(max_retries=3, sleep_time=5):
    """
    网络错误重试装饰器
    
    :param max_retries: int, 最大重试次数，默认3
    :param sleep_time: int, 重试前等待时间（秒），默认5
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            retry_count = 0
            
            while True:
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    msg = str(e)
                    
                    # 判断是否是网络相关错误
                    network_keywords = [
                        "连接", "网络", "timeout", "超时", "connection",
                        "网络错误", "连接失败", "请求超时", "网络超时"
                    ]
                    
                    is_network_error = any(keyword in msg for keyword in network_keywords)
                    
                    if is_network_error:
                        retry_count += 1
                        
                        if retry_count > max_retries:
                            print(f"[{func.__name__}] 网络重试次数超过限制({max_retries})，停止重试")
                            raise e
                        
                        print(f"[{func.__name__}] 网络错误，第{retry_count}次重试，等待{sleep_time}秒...")
                        time.sleep(sleep_time)
                        continue
                    else:
                        print(f"[{func.__name__}] 非网络错误: {msg}")
                        raise e
        
        return wrapper
    return decorator

def smart_retry(max_retries=5, limit_sleep=120, network_sleep=10):
    """
    智能重试装饰器（同时处理频限和网络错误）
    
    :param max_retries: int, 最大重试次数，默认5
    :param limit_sleep: int, 频限错误等待时间（秒），默认120
    :param network_sleep: int, 网络错误等待时间（秒），默认10
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            retry_count = 0
            
            while True:
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    msg = str(e)
                    retry_count += 1
                    
                    # 判断错误类型
                    limit_keywords = [
                        "每分钟最多访问", "频次", "频率", "访问过于频繁",
                        "rate limit", "too many requests", "quota exceeded",
                        "访问次数超限", "请求过于频繁", "API调用频率超限"
                    ]
                    
                    network_keywords = [
                        "连接", "网络", "timeout", "超时", "connection",
                        "网络错误", "连接失败", "请求超时", "网络超时"
                    ]
                    
                    is_limit_error = any(keyword in msg for keyword in limit_keywords)
                    is_network_error = any(keyword in msg for keyword in network_keywords)
                    
                    if retry_count > max_retries:
                        print(f"[{func.__name__}] 重试次数超过限制({max_retries})，停止重试")
                        raise e
                    
                    if is_limit_error:
                        # 对于限流错误，使用递增的等待时间
                        actual_sleep = limit_sleep + (retry_count - 1) * 30 + random.uniform(0, 60)
                        print(f"[{func.__name__}] 访问频次限制，第{retry_count}次重试，等待{actual_sleep:.1f}秒...")
                        time.sleep(actual_sleep)
                        continue
                    elif is_network_error:
                        actual_sleep = network_sleep + random.uniform(0, 10)
                        print(f"[{func.__name__}] 网络错误，第{retry_count}次重试，等待{actual_sleep:.1f}秒...")
                        time.sleep(actual_sleep)
                        continue
                    else:
                        print(f"[{func.__name__}] 其他错误: {msg}")
                        raise e
        
        return wrapper
    return decorator

def tushare_retry(max_retries=10, base_sleep=60):
    """
    专门针对Tushare API的重试装饰器
    
    :param max_retries: int, 最大重试次数，默认10
    :param base_sleep: int, 基础等待时间（秒），默认60
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            retry_count = 0
            
            while True:
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    msg = str(e)
                    retry_count += 1
                    
                    # Tushare特有的限流错误
                    tushare_limit_keywords = [
                        "每分钟最多访问", "访问次数超限", "请求过于频繁"
                    ]
                    
                    is_tushare_limit = any(keyword in msg for keyword in tushare_limit_keywords)
                    
                    if retry_count > max_retries:
                        print(f"[{func.__name__}] Tushare重试次数超过限制({max_retries})，停止重试")
                        raise e
                    
                    if is_tushare_limit:
                        # 使用递增等待时间：60s, 75s, 90s, 105s...
                        sleep_time = base_sleep + (retry_count - 1) * 15 + random.uniform(0, 10)
                        print(f"[{func.__name__}] Tushare限流，第{retry_count}次重试，等待{sleep_time:.1f}秒...")
                        time.sleep(sleep_time)
                        continue
                    else:
                        print(f"[{func.__name__}] 非Tushare限流错误: {msg}")
                        raise e
        
        return wrapper
    return decorator

# 使用示例
if __name__ == "__main__":
    import tushare as ts
    import pandas as pd
    
    # 设置tushare token
    ts.set_token('your_token_here')
    pro = ts.pro_api()
    
    # 示例1: 使用频限重试装饰器
    @retry_on_limit()
    def get_daily_data(ts_code, start_date, end_date):
        df = pro.daily(ts_code=ts_code, start_date=start_date, end_date=end_date)
        return df
    
    # 示例2: 使用智能重试装饰器
    @smart_retry(max_retries=3)
    def get_stock_basic(ts_code):
        df = pro.stock_basic(ts_code=ts_code)
        return df
    
    # 示例3: 使用Tushare专用重试装饰器
    @tushare_retry(max_retries=5, base_sleep=60)
    def get_fund_share(ts_code):
        df = pro.fund_share(ts_code=ts_code)
        return df
    
    # 示例4: 自定义重试参数
    @retry_on_limit(max_retries=5, sleep_time=30)
    def get_index_weight(index_code, start_date, end_date):
        df = pro.index_weight(index_code=index_code, start_date=start_date, end_date=end_date)
        return df 