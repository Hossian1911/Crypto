"""
通用多线程加速工具
"""

import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

def print_progress_bar(current=0, total=0, bar_length=50):
    """
    打印进度条
    
    :param current: int, 当前进度
    :param total: int, 总数量
    :param bar_length: int, 进度条长度，默认50
    """
    progress = current / total
    filled_length = int(bar_length * progress)
    bar = '█' * filled_length + '-' * (bar_length - filled_length)
    percentage = progress * 100
    
    sys.stdout.write(f'\r进度: [{bar}] {percentage:.1f}% ({current}/{total})')
    sys.stdout.flush()
    
    if current == total:
        print()

def run_multithread(func=None, data_list=None, max_workers=8, show_progress=True):
    """
    通用多线程执行函数
    
    :param func: function, 要执行的函数，接收单个数据项作为参数
    :param data_list: list, 数据列表
    :param max_workers: int, 线程数，默认8
    :param show_progress: bool, 是否显示进度条，默认True
    :return: list, 函数执行结果列表
    """
    results = [None] * len(data_list)  # 预分配结果列表，保持顺序
    completed = 0
    total = len(data_list)
    
    if show_progress:
        print(f"使用 {max_workers} 个线程处理 {total} 个任务...")
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # 提交所有任务，并记录索引
        future_to_index = {executor.submit(func, data): i for i, data in enumerate(data_list)}
        
        # 处理完成的任务
        for future in as_completed(future_to_index):
            index = future_to_index[future]
            try:
                result = future.result()
                results[index] = result  # 按原始索引放置结果
                completed += 1
                
                if show_progress:
                    print_progress_bar(completed, total)
                    
            except Exception as e:
                print(f"\n任务处理失败: {str(e)}")
                completed += 1
                if show_progress:
                    print_progress_bar(completed, total)
    
    # 过滤掉None结果
    filtered_results = [r for r in results if r is not None]
    return filtered_results 