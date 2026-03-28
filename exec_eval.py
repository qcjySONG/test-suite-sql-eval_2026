import os
import re
import asyncio
import sqlite3
import threading
from typing import Tuple, Any, List, Set
from itertools import product
from collections import defaultdict
import tqdm
import random
import time
import pickle as pkl
import subprocess
from itertools import chain



threadLock = threading.Lock()
TIMEOUT = 30
EXEC_TMP_DIR = 'tmp/'

def permute_tuple(element: Tuple, perm: Tuple) -> Tuple:
    assert len(element) == len(perm)
    return tuple([element[i] for i in perm])


def unorder_row(row: Tuple, round_values: bool = False, decimal_places: int = 4) -> Tuple:
    """
    对行进行排序，支持数值舍入
    
    Args:
        row: 行数据
        round_values: 是否对数值进行舍入
        decimal_places: 小数位数（默认4位）
    """
    def sort_key(x):
        # 如果启用舍入且是数值类型，进行舍入处理
        if round_values and isinstance(x, (int, float)):
            rounded_value = round(float(x), decimal_places)
            return str(rounded_value) + str(type(x))
        return str(x) + str(type(x))
    
    return tuple(sorted(row, key=sort_key))


# unorder each row in the table
# [result_1 and result_2 has the same bag of unordered row]
# is a necessary condition of
# [result_1 and result_2 are equivalent in denotation]
def quick_rej(result1: List[Tuple], result2: List[Tuple], order_matters: bool, 
              round_values: bool = False, decimal_places: int = 4) -> bool:
    s1 = [unorder_row(row, round_values, decimal_places) for row in result1]
    s2 = [unorder_row(row, round_values, decimal_places) for row in result2]
    if order_matters:
        return s1 == s2
    else:
        return set(s1) == set(s2)


# return whether two bag of relations are equivalent
def multiset_eq(l1: List, l2: List) -> bool:
    if len(l1) != len(l2):
        return False
    d = defaultdict(int)
    for e in l1:
        d[e] = d[e] + 1
    for e in l2:
        d[e] = d[e] - 1
        if d[e] < 0:
            return False
    return True


def get_constraint_permutation(tab1_sets_by_columns: List[Set], result2: List[Tuple]):
    num_cols = len(result2[0])
    perm_constraints = [{i for i in range(num_cols)} for _ in range(num_cols)]
    if num_cols <= 3:
        return product(*perm_constraints)

    # we sample 20 rows and constrain the space of permutations
    for _ in range(20):
        random_tab2_row = random.choice(result2)

        for tab1_col in range(num_cols):
            for tab2_col in set(perm_constraints[tab1_col]):
                if random_tab2_row[tab2_col] not in tab1_sets_by_columns[tab1_col]:
                    perm_constraints[tab1_col].remove(tab2_col)
    return product(*perm_constraints)


# check whether two denotations are correct
def result_eq(result1: List[Tuple], result2: List[Tuple], order_matters: bool,
              round_values: bool = False, decimal_places: int = 4) -> bool:
    if len(result1) == 0 and len(result2) == 0:
        return True

    # if length is not the same, then they are definitely different bag of rows
    if len(result1) != len(result2):
        return False

    num_cols = len(result1[0])

    # if the results do not have the same number of columns, they are different
    if len(result2[0]) != num_cols:
        return False

    # unorder each row and compare whether the denotation is the same
    # this can already find most pair of denotations that are different
    if not quick_rej(result1, result2, order_matters, round_values, decimal_places):
        return False

    # the rest of the problem is in fact more complicated than one might think
    # we want to find a permutation of column order and a permutation of row order,
    # s.t. result_1 is the same as result_2
    # we return true if we can find such column & row permutations
    # and false if we cannot
    tab1_sets_by_columns = [{row[i] for row in result1} for i in range(num_cols)]

    # on a high level, we enumerate all possible column permutations that might make result_1 == result_2
    # we decrease the size of the column permutation space by the function get_constraint_permutation
    # if one of the permutation make result_1, result_2 equivalent, then they are equivalent
    for perm in get_constraint_permutation(tab1_sets_by_columns, result2):
        if len(perm) != len(set(perm)):
            continue
        if num_cols == 1:
            result2_perm = result2
        else:
            result2_perm = [permute_tuple(element, perm) for element in result2]
        if order_matters:
            if result1 == result2_perm:
                return True
        else:
            # in fact the first condition must hold if the second condition holds
            # but the first is way more efficient implementation-wise
            # and we use it to quickly reject impossible candidates
            if set(result1) == set(result2_perm) and multiset_eq(result1, result2_perm):
                return True
    return False


def replace_cur_year(query: str) -> str:
    return re.sub(
        "YEAR\s*\(\s*CURDATE\s*\(\s*\)\s*\)\s*", "2020", query, flags=re.IGNORECASE
    )


# get the database cursor for a sqlite database path
def get_cursor_from_path(sqlite_path: str):
    try:
        if not os.path.exists(sqlite_path):
            print("Openning a new connection %s" % sqlite_path)
        connection = sqlite3.connect(sqlite_path)
    except Exception as e:
        print(sqlite_path)
        raise e
    connection.text_factory = lambda b: b.decode(errors="ignore")
    cursor = connection.cursor()
    return cursor


def exec_on_db(
    sqlite_path: str, query: str, timeout: int = TIMEOUT
) -> Tuple[str, Any]:
    """
    使用 threading.Timer 和 conn.interrupt() 为 SQLite 查询提供超时保护。
    这是一个同步函数。
    """
    query = replace_cur_year(query)
    
    try:
        # 允许在不同线程中使用同一个连接对象，这是 conn.interrupt() 的前提
        # 使用 URI 模式并设置为只读，是更安全的方式
        con = sqlite3.connect(f"file:{sqlite_path}?mode=ro", uri=True, check_same_thread=False)
        cursor = con.cursor()

        # 设置定时器，在超时后调用 con.interrupt()
        timer = threading.Timer(timeout, lambda: con.interrupt())
        timer.start()

        try:
            cursor.execute(query)
            result = cursor.fetchall()
            return "result", result
        except (sqlite3.OperationalError, sqlite3.DatabaseError) as e:
            # 捕获中断异常或其他数据库错误
            if "interrupted" in str(e).lower() or "interrupt" in str(e).lower():
                return 'exception', TimeoutError(f'Query timed out after {timeout} seconds')
            return "exception", e
        finally:
            # 确保定时器被取消，并且连接被关闭
            timer.cancel()
            cursor.close()
            con.close()

    except Exception as e:
        # 捕获连接数据库时可能发生的错误
        return "exception", e


# postprocess the model predictions to avoid execution errors
# e.g. removing spaces between ">" and "="
def postprocess(query: str) -> str:
    query = query.replace('> =', '>=').replace('< =', '<=').replace('! =', '!=')
    return query


# approximate whether p_str and g_str are semantically equivalent
# db is the database path
# we are going to evaluate whether they are equivalent in all the databases
# that are in the same directory as db
# 0 if denotationally equivalent
# 1 otherwise
# the meaning of each auxillary argument can be seen in the parser definition in evaluation.py
def eval_exec_match(db: str, p_str: str, g_str: str, plug_value: bool, keep_distinct: bool, 
                    progress_bar_for_each_datapoint: bool, round_values: bool = False, 
                    decimal_places: int = 4) -> int:
    p_str, g_str = postprocess(p_str), postprocess(g_str)

    order_matters = 'order by' in g_str.lower()

    db_dir = os.path.dirname(db)
    db_paths = [os.path.join(db_dir, basename) for basename in os.listdir(db_dir) if '.sqlite' in basename]

    for pred in [p_str]:
        pred_passes = 1
        ranger = tqdm.tqdm(db_paths) if progress_bar_for_each_datapoint else db_paths

        for db_path in ranger:
            g_flag, g_denotation = exec_on_db(db_path, g_str)
            p_flag, p_denotation = exec_on_db(db_path, pred)

            if g_flag == 'exception':
                print(f"Gold query failed on {db_path}: {g_str}")
                print(f"Error: {g_denotation}")
                assert g_flag != 'exception', 'gold query %s has error on database file %s' % (g_str, db_path)

            if p_flag == 'exception':
                pred_passes = 0
            elif not result_eq(g_denotation, p_denotation, order_matters=order_matters, 
                             round_values=round_values, decimal_places=decimal_places):
                pred_passes = 0
            
            if pred_passes == 0:
                break

        if pred_passes == 1:
            return 1

    return 0
