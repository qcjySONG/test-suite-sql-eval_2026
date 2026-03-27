import os
import json
import sqlite3
import argparse

import sqlglot
from sqlglot.expressions import Table, CTE

from process_sql import get_schema, Schema, get_sql
from exec_eval import eval_exec_match

# Flag to disable value evaluation
DISABLE_VALUE = True
# Flag to disable distinct in select evaluation
DISABLE_DISTINCT = True


CLAUSE_KEYWORDS = ('select', 'from', 'where', 'group', 'order', 'limit', 'intersect', 'union', 'except')
JOIN_KEYWORDS = ('join', 'on', 'as')

WHERE_OPS = ('not', 'between', '=', '>', '<', '>=', '<=', '!=', 'in', 'like', 'is', 'exists')
UNIT_OPS = ('none', '-', '+', "*", '/')
AGG_OPS = ('none', 'max', 'min', 'count', 'sum', 'avg')
TABLE_TYPE = {
    'sql': "sql",
    'table_unit': "table_unit",
}

COND_OPS = ('and', 'or')
SQL_OPS = ('intersect', 'union', 'except')
ORDER_OPS = ('desc', 'asc')


HARDNESS = {
    "component1": ('where', 'group', 'order', 'limit', 'join', 'or', 'like'),
    "component2": ('except', 'union', 'intersect')
}


def condition_has_or(conds):
    return 'or' in conds[1::2]


def condition_has_like(conds):
    return WHERE_OPS.index('like') in [cond_unit[1] for cond_unit in conds[::2]]


def condition_has_sql(conds):
    for cond_unit in conds[::2]:
        val1, val2 = cond_unit[3], cond_unit[4]
        if val1 is not None and type(val1) is dict:
            return True
        if val2 is not None and type(val2) is dict:
            return True
    return False


def val_has_op(val_unit):
    return val_unit[0] != UNIT_OPS.index('none')


def has_agg(unit):
    return unit[0] != AGG_OPS.index('none')


def accuracy(count, total):
    if count == total:
        return 1
    return 0


def recall(count, total):
    if count == total:
        return 1
    return 0


def F1(acc, rec):
    if (acc + rec) == 0:
        return 0
    return (2. * acc * rec) / (acc + rec)


def get_scores(count, pred_total, label_total):
    if pred_total != label_total:
        return 0,0,0
    elif count == pred_total:
        return 1,1,1
    return 0,0,0

def load_json_data(json_filepath):
    """
    读取包含 gold 和 predict 的单一 JSON 文件。
    返回 glist 和 plist，格式与原评估脚本内部结构兼容。
    """
    with open(json_filepath, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    glist = []
    plist = []
    
    for item in data:
        # 获取数据库ID，如果没有默认为空字符串（会导致后续加载Schema失败，所以必须有）
        db_id = item.get('db_id', item.get('db', ''))
        
        # 获取 SQL 列表
        # 兼容用户自定义的键名，比如 "预测sql", "goldsql" 或者标准的 "predicted_sqls"
        p_sqls = item.get('predicted_sqls', item.get('预测sql', []))
        g_sqls = item.get('gold_sqls', item.get('goldsql', []))
        
        # 构造 glist: [[[sql, db], [sql, db]], ...]
        # Gold 需要包含 db_id 用于后续 Schema 加载
        g_session = []
        for g_sql in g_sqls:
            # 确保 SQL 是字符串
            g_session.append([str(g_sql), db_id])
        glist.append(g_session)
        
        # 构造 plist: [[[sql], [sql]], ...]
        # Predict 只需要 SQL 字符串
        p_session = []
        for p_sql in p_sqls:
            p_session.append([str(p_sql)])
        plist.append(p_session)
        
    return glist, plist


def eval_sel(pred, label):
    pred_sel = pred['select'][1]
    label_sel = label['select'][1]
    label_wo_agg = [unit[1] for unit in label_sel]
    pred_total = len(pred_sel)
    label_total = len(label_sel)
    cnt = 0
    cnt_wo_agg = 0

    for unit in pred_sel:
        if unit in label_sel:
            cnt += 1
            label_sel.remove(unit)
        if unit[1] in label_wo_agg:
            cnt_wo_agg += 1
            label_wo_agg.remove(unit[1])

    return label_total, pred_total, cnt, cnt_wo_agg


def eval_where(pred, label):
    pred_conds = [unit for unit in pred['where'][::2]]
    label_conds = [unit for unit in label['where'][::2]]
    label_wo_agg = [unit[2] for unit in label_conds]
    pred_total = len(pred_conds)
    label_total = len(label_conds)
    cnt = 0
    cnt_wo_agg = 0

    for unit in pred_conds:
        if unit in label_conds:
            cnt += 1
            label_conds.remove(unit)
        if unit[2] in label_wo_agg:
            cnt_wo_agg += 1
            label_wo_agg.remove(unit[2])

    return label_total, pred_total, cnt, cnt_wo_agg


def eval_group(pred, label):
    pred_cols = [unit[1] for unit in pred['groupBy']]
    label_cols = [unit[1] for unit in label['groupBy']]
    pred_total = len(pred_cols)
    label_total = len(label_cols)
    cnt = 0
    pred_cols = [pred.split(".")[1] if "." in pred else pred for pred in pred_cols]
    label_cols = [label.split(".")[1] if "." in label else label for label in label_cols]
    for col in pred_cols:
        if col in label_cols:
            cnt += 1
            label_cols.remove(col)
    return label_total, pred_total, cnt


def eval_having(pred, label):
    pred_total = label_total = cnt = 0
    if len(pred['groupBy']) > 0:
        pred_total = 1
    if len(label['groupBy']) > 0:
        label_total = 1

    pred_cols = [unit[1] for unit in pred['groupBy']]
    label_cols = [unit[1] for unit in label['groupBy']]
    if pred_total == label_total == 1 \
            and pred_cols == label_cols \
            and pred['having'] == label['having']:
        cnt = 1

    return label_total, pred_total, cnt


def eval_order(pred, label):
    pred_total = label_total = cnt = 0
    if len(pred['orderBy']) > 0:
        pred_total = 1
    if len(label['orderBy']) > 0:
        label_total = 1
    if len(label['orderBy']) > 0 and pred['orderBy'] == label['orderBy'] and \
            ((pred['limit'] is None and label['limit'] is None) or (pred['limit'] is not None and label['limit'] is not None)):
        cnt = 1
    return label_total, pred_total, cnt


def eval_and_or(pred, label):
    pred_ao = pred['where'][1::2]
    label_ao = label['where'][1::2]
    pred_ao = set(pred_ao)
    label_ao = set(label_ao)

    if pred_ao == label_ao:
        return 1,1,1
    return len(pred_ao),len(label_ao),0


def get_nestedSQL(sql):
    nested = []
    for cond_unit in sql['from']['conds'][::2] + sql['where'][::2] + sql['having'][::2]:
        if type(cond_unit[3]) is dict:
            nested.append(cond_unit[3])
        if type(cond_unit[4]) is dict:
            nested.append(cond_unit[4])
    if sql['intersect'] is not None:
        nested.append(sql['intersect'])
    if sql['except'] is not None:
        nested.append(sql['except'])
    if sql['union'] is not None:
        nested.append(sql['union'])
    return nested


def eval_nested(pred, label):
    label_total = 0
    pred_total = 0
    cnt = 0
    if pred is not None:
        pred_total += 1
    if label is not None:
        label_total += 1
    if pred is not None and label is not None:
        cnt += Evaluator().eval_exact_match(pred, label)
    return label_total, pred_total, cnt


def eval_IUEN(pred, label):
    lt1, pt1, cnt1 = eval_nested(pred['intersect'], label['intersect'])
    lt2, pt2, cnt2 = eval_nested(pred['except'], label['except'])
    lt3, pt3, cnt3 = eval_nested(pred['union'], label['union'])
    label_total = lt1 + lt2 + lt3
    pred_total = pt1 + pt2 + pt3
    cnt = cnt1 + cnt2 + cnt3
    return label_total, pred_total, cnt


def get_keywords(sql):
    res = set()
    if len(sql['where']) > 0:
        res.add('where')
    if len(sql['groupBy']) > 0:
        res.add('group')
    if len(sql['having']) > 0:
        res.add('having')
    if len(sql['orderBy']) > 0:
        res.add(sql['orderBy'][0])
        res.add('order')
    if sql['limit'] is not None:
        res.add('limit')
    if sql['except'] is not None:
        res.add('except')
    if sql['union'] is not None:
        res.add('union')
    if sql['intersect'] is not None:
        res.add('intersect')

    # or keyword
    ao = sql['from']['conds'][1::2] + sql['where'][1::2] + sql['having'][1::2]
    if len([token for token in ao if token == 'or']) > 0:
        res.add('or')

    cond_units = sql['from']['conds'][::2] + sql['where'][::2] + sql['having'][::2]
    # not keyword
    if len([cond_unit for cond_unit in cond_units if cond_unit[0]]) > 0:
        res.add('not')

    # in keyword
    if len([cond_unit for cond_unit in cond_units if cond_unit[1] == WHERE_OPS.index('in')]) > 0:
        res.add('in')

    # like keyword
    if len([cond_unit for cond_unit in cond_units if cond_unit[1] == WHERE_OPS.index('like')]) > 0:
        res.add('like')

    return res


def eval_keywords(pred, label):
    pred_keywords = get_keywords(pred)
    label_keywords = get_keywords(label)
    pred_total = len(pred_keywords)
    label_total = len(label_keywords)
    cnt = 0

    for k in pred_keywords:
        if k in label_keywords:
            cnt += 1
    return label_total, pred_total, cnt


def count_agg(units):
    return len([unit for unit in units if has_agg(unit)])


def count_component1(sql):
    count = 0
    if len(sql['where']) > 0:
        count += 1
    if len(sql['groupBy']) > 0:
        count += 1
    if len(sql['orderBy']) > 0:
        count += 1
    if sql['limit'] is not None:
        count += 1
    if len(sql['from']['table_units']) > 0:  # JOIN
        count += len(sql['from']['table_units']) - 1

    ao = sql['from']['conds'][1::2] + sql['where'][1::2] + sql['having'][1::2]
    count += len([token for token in ao if token == 'or'])
    cond_units = sql['from']['conds'][::2] + sql['where'][::2] + sql['having'][::2]
    count += len([cond_unit for cond_unit in cond_units if cond_unit[1] == WHERE_OPS.index('like')])

    return count


def count_component2(sql):
    nested = get_nestedSQL(sql)
    return len(nested)


def count_others(sql):
    count = 0
    # number of aggregation
    agg_count = count_agg(sql['select'][1])
    agg_count += count_agg(sql['where'][::2])
    agg_count += count_agg(sql['groupBy'])
    if len(sql['orderBy']) > 0:
        agg_count += count_agg([unit[1] for unit in sql['orderBy'][1] if unit[1]] +
                            [unit[2] for unit in sql['orderBy'][1] if unit[2]])
    agg_count += count_agg(sql['having'])
    if agg_count > 1:
        count += 1

    # number of select columns
    if len(sql['select'][1]) > 1:
        count += 1

    # number of where conditions
    if len(sql['where']) > 1:
        count += 1

    # number of group by clauses
    if len(sql['groupBy']) > 1:
        count += 1

    return count


class Evaluator:
    """A simple evaluator"""
    def __init__(self):
        self.partial_scores = None

    def eval_hardness(self, sql):
        count_comp1_ = count_component1(sql)
        count_comp2_ = count_component2(sql)
        count_others_ = count_others(sql)

        if count_comp1_ <= 1 and count_others_ == 0 and count_comp2_ == 0:
            return "easy"
        elif (count_others_ <= 2 and count_comp1_ <= 1 and count_comp2_ == 0) or \
                (count_comp1_ <= 2 and count_others_ < 2 and count_comp2_ == 0):
            return "medium"
        elif (count_others_ > 2 and count_comp1_ <= 2 and count_comp2_ == 0) or \
                (2 < count_comp1_ <= 3 and count_others_ <= 2 and count_comp2_ == 0) or \
                (count_comp1_ <= 1 and count_others_ == 0 and count_comp2_ <= 1):
            return "hard"
        else:
            return "extra"

    def eval_exact_match(self, pred, label):
        partial_scores = self.eval_partial_match(pred, label)
        self.partial_scores = partial_scores

        for key, score in partial_scores.items():
            if score['f1'] != 1:
                return 0

        if len(label['from']['table_units']) > 0:
            label_tables = sorted(label['from']['table_units'])
            pred_tables = sorted(pred['from']['table_units'])
            return label_tables == pred_tables
        return 1

    def eval_partial_match(self, pred, label):
        res = {}

        label_total, pred_total, cnt, cnt_wo_agg = eval_sel(pred, label)
        acc, rec, f1 = get_scores(cnt, pred_total, label_total)
        res['select'] = {'acc': acc, 'rec': rec, 'f1': f1,'label_total':label_total,'pred_total':pred_total}
        acc, rec, f1 = get_scores(cnt_wo_agg, pred_total, label_total)
        res['select(no AGG)'] = {'acc': acc, 'rec': rec, 'f1': f1,'label_total':label_total,'pred_total':pred_total}

        label_total, pred_total, cnt, cnt_wo_agg = eval_where(pred, label)
        acc, rec, f1 = get_scores(cnt, pred_total, label_total)
        res['where'] = {'acc': acc, 'rec': rec, 'f1': f1,'label_total':label_total,'pred_total':pred_total}
        acc, rec, f1 = get_scores(cnt_wo_agg, pred_total, label_total)
        res['where(no OP)'] = {'acc': acc, 'rec': rec, 'f1': f1,'label_total':label_total,'pred_total':pred_total}

        label_total, pred_total, cnt = eval_group(pred, label)
        acc, rec, f1 = get_scores(cnt, pred_total, label_total)
        res['group(no Having)'] = {'acc': acc, 'rec': rec, 'f1': f1,'label_total':label_total,'pred_total':pred_total}

        label_total, pred_total, cnt = eval_having(pred, label)
        acc, rec, f1 = get_scores(cnt, pred_total, label_total)
        res['group'] = {'acc': acc, 'rec': rec, 'f1': f1,'label_total':label_total,'pred_total':pred_total}

        label_total, pred_total, cnt = eval_order(pred, label)
        acc, rec, f1 = get_scores(cnt, pred_total, label_total)
        res['order'] = {'acc': acc, 'rec': rec, 'f1': f1,'label_total':label_total,'pred_total':pred_total}

        label_total, pred_total, cnt = eval_and_or(pred, label)
        acc, rec, f1 = get_scores(cnt, pred_total, label_total)
        res['and/or'] = {'acc': acc, 'rec': rec, 'f1': f1,'label_total':label_total,'pred_total':pred_total}

        label_total, pred_total, cnt = eval_IUEN(pred, label)
        acc, rec, f1 = get_scores(cnt, pred_total, label_total)
        res['IUEN'] = {'acc': acc, 'rec': rec, 'f1': f1,'label_total':label_total,'pred_total':pred_total}

        label_total, pred_total, cnt = eval_keywords(pred, label)
        acc, rec, f1 = get_scores(cnt, pred_total, label_total)
        res['keywords'] = {'acc': acc, 'rec': rec, 'f1': f1,'label_total':label_total,'pred_total':pred_total}

        return res


def isValidSQL(sql, db):
    conn = sqlite3.connect(db)
    cursor = conn.cursor()
    try:
        cursor.execute(sql)
    except:
        return False
    return True



def print_formated_s(row_name, l, element_format):
    template = "{:20} " + ' '.join([element_format] * len(l))
    print(template.format(row_name, *l))


def print_scores(scores, etype, include_turn_acc=True):
    turns = ['turn 1', 'turn 2', 'turn 3', 'turn 4', 'turn > 4']
    levels = ['easy', 'medium', 'hard', 'extra', 'all']
    if include_turn_acc:
        levels.append('joint_all')
    partial_types = ['select', 'select(no AGG)', 'where', 'where(no OP)', 'group(no Having)',
                     'group', 'order', 'and/or', 'IUEN', 'keywords']

    print_formated_s("", levels, '{:20}')
    counts = [scores[level]['count'] for level in levels]
    print_formated_s("count", counts, '{:<20d}')

    if etype in ["all", "exec"]:
        print ('=====================   EXECUTION ACCURACY     =====================')
        exec_scores = [scores[level]['exec'] for level in levels]
        print_formated_s("execution", exec_scores, '{:<20.3f}')

    if etype in ["all", "match"]:
        print ('\n====================== EXACT MATCHING ACCURACY =====================')
        exact_scores = [scores[level]['exact'] for level in levels]
        print_formated_s("exact match", exact_scores, '{:<20.3f}')
        print ('\n---------------------PARTIAL MATCHING ACCURACY----------------------')
        for type_ in partial_types:
            this_scores = [scores[level]['partial'][type_]['acc'] for level in levels]
            print_formated_s(type_, this_scores, '{:<20.3f}')

        print ('---------------------- PARTIAL MATCHING RECALL ----------------------')
        for type_ in partial_types:
            this_scores = [scores[level]['partial'][type_]['rec'] for level in levels]
            print_formated_s(type_, this_scores, '{:<20.3f}')

        print ('---------------------- PARTIAL MATCHING F1 --------------------------')
        for type_ in partial_types:
            this_scores = [scores[level]['partial'][type_]['f1'] for level in levels]
            print_formated_s(type_, this_scores, '{:<20.3f}')

    if include_turn_acc:
        print()
        print()
        print_formated_s("", turns, '{:20}')
        counts = [scores[turn]['count'] for turn in turns]
        print_formated_s("count", counts, "{:<20d}")

        if etype in ["all", "exec"]:
            print ('=====================   TURN EXECUTION ACCURACY     =====================')
            exec_scores = [scores[turn]['exec'] for turn in turns]
            print_formated_s("execution", exec_scores, '{:<20.3f}')

        if etype in ["all", "match"]:
            print ('\n====================== TURN EXACT MATCHING ACCURACY =====================')
            exact_scores = [scores[turn]['exact'] for turn in turns]
            print_formated_s("exact match", exact_scores, '{:<20.3f}')

    # 新增：打印表数量维度的统计
    if etype in ["all", "exec"]:
        print()
        print()
        table_levels = ['single', 'two', 'three', 'four', 'five_plus', 'all']
        print_formated_s("", table_levels, '{:20}')
        table_counts = [scores['table_counts'][level]['count'] for level in table_levels]
        print_formated_s("count", table_counts, '{:<20d}')
        print ('=====================   TABLE COUNT EXECUTION ACCURACY     =====================')
        table_exec_scores = [scores['table_counts'][level]['exec'] for level in table_levels]
        print_formated_s("execution", table_exec_scores, '{:<20.3f}')


def get_table_count(sql_str: str, dialect: str = "sqlite") -> int:
    """
    统计 SQL 语句中真实表的个数（不包含 CTE 中定义的临时表）

    Args:
        sql_str: SQL 语句字符串
        dialect: SQL 方言，如 "sqlite", "mysql", "postgres" 等，默认 "sqlite"

    Returns:
        int: 真实表的个数
    """
    try:
        # 解析 SQL 语句
        parsed = sqlglot.parse_one(sql_str, dialect=dialect)
        
        # 收集所有 CTE 的名称（用于排除）
        cte_names = set()
        for cte in parsed.find_all(CTE):
            if cte.alias:
                cte_names.add(cte.alias.lower())  # 统一转小写，避免大小写影响
        
        # 收集所有表名
        table_names = set()
        for table in parsed.find_all(Table):
            # Table 对象可能有 catalog、db、name 等属性，通常我们取 name
            # 有时表名可能带有数据库名，如 db.table，此时 name 是 table
            name = table.name
            if name and name.lower() not in cte_names:
                table_names.add(name.lower())
        
        return len(table_names)
    
    except Exception as e:
        # print(f"解析 SQL 出错: {e}")
        return -1

def get_table_level(table_count):
    """
    根据表数量返回对应的级别
    :param table_count: 表的数量
    :return: 级别字符串
    """
    if table_count == 1:
        return 'single'
    elif table_count == 2:
        return 'two'
    elif table_count == 3:
        return 'three'
    elif table_count == 4:
        return 'four'
    else:
        return 'five_plus'

def evaluate(input_file, db_dir, etype, kmaps, plug_value, keep_distinct, progress_bar_for_each_datapoint, timeout=30):
    """
    修改后的 evaluate 函数
    :param input_file: 包含 gold 和 predict 的 json 文件路径
    :param db_dir: 数据库目录
    ... (其他参数不变)
    """
    
    # 1. 使用新函数加载数据（不再分别读取两个txt）
    # 注意：这里调用的是上面定义的 load_json_data
    glist, plist = load_json_data(input_file)

    # 2. 检查数据对齐
    assert len(plist) == len(glist), "JSON中预测session数量与真实session数量不一致"

    # 3. 这里的逻辑和原代码保持一致：判断是否包含多轮对话
    # 如果任何一个 session 的长度 > 1，则视为多轮，开启 turn accuracy 统计
    include_turn_acc = False
    if len(glist) > 0 and len(glist[0]) > 1:
        include_turn_acc = True

    # --- 以下逻辑基本保持原样，除了变量名微调 ---

    evaluator = Evaluator()
    turns = ['turn 1', 'turn 2', 'turn 3', 'turn 4', 'turn > 4']
    levels = ['easy', 'medium', 'hard', 'extra', 'all', 'joint_all']
    table_levels = ['single', 'two', 'three', 'four', 'five_plus', 'all']

    partial_types = ['select', 'select(no AGG)', 'where', 'where(no OP)', 'group(no Having)',
                     'group', 'order', 'and/or', 'IUEN', 'keywords']
    entries = []
    scores = {}

    for turn in turns:
        scores[turn] = {'count': 0, 'exact': 0.}
        scores[turn]['exec'] = 0

    for level in levels:
        scores[level] = {'count': 0, 'partial': {}, 'exact': 0.}
        scores[level]['exec'] = 0
        for type_ in partial_types:
            scores[level]['partial'][type_] = {'acc': 0., 'rec': 0., 'f1': 0.,'acc_count':0,'rec_count':0}

    # 初始化表数量维度的统计
    scores['table_counts'] = {}
    for t_level in table_levels:
        scores['table_counts'][t_level] = {'count': 0, 'exec': 0}

    for i, (p, g) in enumerate(zip(plist, glist)):
        if (i + 1) % 10 == 0:
            print('Evaluating %dth prediction' % (i + 1))
        scores['joint_all']['count'] += 1
        turn_scores = {"exec": [], "exact": []}
        for idx, pg in enumerate(zip(p, g)):
            p, g = pg
            # p 的格式现在是 [sql_str]
            p_str = p[0]
            # g 的格式现在是 [sql_str, db_id]
            g_str, db_name = g
            
            # 这里如果不做替换，有些模型预测 output 会包含 value 占位符
            p_str = p_str.replace("value", "1")

            db = os.path.join(db_dir, db_name, db_name + ".sqlite")
            schema = Schema(get_schema(db))
            # --- 修改开始 ---
            try:
                # 尝试解析 Gold SQL 以获取难度分级
                g_sql = get_sql(schema, g_str)
                hardness = evaluator.eval_hardness(g_sql)
            except:
                # 如果解析失败（通常是因为包含特殊语法），直接设为默认难度，不影响 EX 计算
                # 这里的 g_sql 设为 None 不会影响 EX，因为 eval_exec_match 用的是字符串 g_str
                g_sql = None 
                hardness = "extra" 
            # --- 修改结束 ---
                
            # g_sql = get_sql(schema, g_str)
            # hardness = evaluator.eval_hardness(g_sql)
            
            if idx > 3:
                turn_idx_str = "turn > 4"
            else:
                turn_idx_str = "turn " + str(idx + 1)
            
            scores[turn_idx_str]['count'] += 1
            scores[hardness]['count'] += 1
            scores['all']['count'] += 1

            try:
                p_sql = get_sql(schema, p_str)
            except:
                # 解析失败时的兜底空 SQL
                p_sql = {
                "except": None,
                "from": {"conds": [], "table_units": []},
                "groupBy": [],
                "having": [],
                "intersect": None,
                "limit": None,
                "orderBy": [],
                "select": [False, []],
                "union": None,
                "where": []
                }

            if etype in ["all", "exec"]:
                exec_score = eval_exec_match(db=db, p_str=p_str, g_str=g_str, plug_value=plug_value,
                                             keep_distinct=keep_distinct, progress_bar_for_each_datapoint=progress_bar_for_each_datapoint)
                if exec_score:
                    scores[hardness]['exec'] += 1
                    scores[turn_idx_str]['exec'] += 1
                    scores['all']['exec'] += 1
                    turn_scores['exec'].append(1)
                else:
                    turn_scores['exec'].append(0)

                # 新增：统计表数量维度
                # 使用 sqlglot 直接解析原始 SQL 字符串来统计真实表数量
                table_count = get_table_count(g_str)
                if table_count >= 0:  # 解析成功
                    table_level = get_table_level(table_count)
                    scores['table_counts'][table_level]['count'] += 1
                    scores['table_counts'][table_level]['exec'] += exec_score
                    scores['table_counts']['all']['count'] += 1
                    scores['table_counts']['all']['exec'] += exec_score
                else:
                    # 解析失败，将其归类为 'five_plus'（多表）
                    scores['table_counts']['five_plus']['count'] += 1
                    scores['table_counts']['five_plus']['exec'] += exec_score
                    scores['table_counts']['all']['count'] += 1
                    scores['table_counts']['all']['exec'] += exec_score

            if etype in ["all", "match"]:
                kmap = kmaps[db_name]
                g_valid_col_units = build_valid_col_units(g_sql['from']['table_units'], schema)
                g_sql = rebuild_sql_val(g_sql)
                g_sql = rebuild_sql_col(g_valid_col_units, g_sql, kmap)
                p_valid_col_units = build_valid_col_units(p_sql['from']['table_units'], schema)
                p_sql = rebuild_sql_val(p_sql)
                p_sql = rebuild_sql_col(p_valid_col_units, p_sql, kmap)
                exact_score = evaluator.eval_exact_match(p_sql, g_sql)
                partial_scores = evaluator.partial_scores
                if exact_score == 0:
                    turn_scores['exact'].append(0)
                    # 可以在这里打印错误日志，方便调试
                    # print("{} pred: {}".format(hardness, p_str))
                    # print("{} gold: {}".format(hardness, g_str))
                    # print("")
                else:
                    turn_scores['exact'].append(1)
                scores[turn_idx_str]['exact'] += exact_score
                scores[hardness]['exact'] += exact_score
                scores['all']['exact'] += exact_score
                
                # 计算 Partial Scores
                for type_ in partial_types:
                    if partial_scores[type_]['pred_total'] > 0:
                        scores[hardness]['partial'][type_]['acc'] += partial_scores[type_]['acc']
                        scores[hardness]['partial'][type_]['acc_count'] += 1
                    if partial_scores[type_]['label_total'] > 0:
                        scores[hardness]['partial'][type_]['rec'] += partial_scores[type_]['rec']
                        scores[hardness]['partial'][type_]['rec_count'] += 1
                    scores[hardness]['partial'][type_]['f1'] += partial_scores[type_]['f1']
                    if partial_scores[type_]['pred_total'] > 0:
                        scores['all']['partial'][type_]['acc'] += partial_scores[type_]['acc']
                        scores['all']['partial'][type_]['acc_count'] += 1
                    if partial_scores[type_]['label_total'] > 0:
                        scores['all']['partial'][type_]['rec'] += partial_scores[type_]['rec']
                        scores['all']['partial'][type_]['rec_count'] += 1
                    scores['all']['partial'][type_]['f1'] += partial_scores[type_]['f1']

                entries.append({
                    'predictSQL': p_str,
                    'goldSQL': g_str,
                    'hardness': hardness,
                    'exact': exact_score,
                    'partial': partial_scores
                })

        if all(v == 1 for v in turn_scores["exec"]):
            scores['joint_all']['exec'] += 1

        if all(v == 1 for v in turn_scores["exact"]):
            scores['joint_all']['exact'] += 1

    # 计算平均分
    for turn in turns:
        if scores[turn]['count'] == 0:
            continue
        if etype in ["all", "exec"]:
            scores[turn]['exec'] /= scores[turn]['count']

        if etype in ["all", "match"]:
            scores[turn]['exact'] /= scores[turn]['count']

    for level in levels:
        if scores[level]['count'] == 0:
            continue
        if etype in ["all", "exec"]:
            scores[level]['exec'] /= scores[level]['count']

        if etype in ["all", "match"]:
            scores[level]['exact'] /= scores[level]['count']
            for type_ in partial_types:
                if scores[level]['partial'][type_]['acc_count'] == 0:
                    scores[level]['partial'][type_]['acc'] = 0
                else:
                    scores[level]['partial'][type_]['acc'] = scores[level]['partial'][type_]['acc'] / \
                                                             scores[level]['partial'][type_]['acc_count'] * 1.0
                if scores[level]['partial'][type_]['rec_count'] == 0:
                    scores[level]['partial'][type_]['rec'] = 0
                else:
                    scores[level]['partial'][type_]['rec'] = scores[level]['partial'][type_]['rec'] / \
                                                             scores[level]['partial'][type_]['rec_count'] * 1.0
                if scores[level]['partial'][type_]['acc'] == 0 and scores[level]['partial'][type_]['rec'] == 0:
                    scores[level]['partial'][type_]['f1'] = 1
                else:
                    scores[level]['partial'][type_]['f1'] = \
                        2.0 * scores[level]['partial'][type_]['acc'] * scores[level]['partial'][type_]['rec'] / (
                        scores[level]['partial'][type_]['rec'] + scores[level]['partial'][type_]['acc'])

    # 计算表数量维度的平均分
    for t_level in table_levels:
        if scores['table_counts'][t_level]['count'] == 0:
            continue
        if etype in ["all", "exec"]:
            scores['table_counts'][t_level]['exec'] /= scores['table_counts'][t_level]['count']

    print_scores(scores, etype, include_turn_acc=include_turn_acc)


# Rebuild SQL functions for value evaluation
def rebuild_cond_unit_val(cond_unit):
    if cond_unit is None or not DISABLE_VALUE:
        return cond_unit

    not_op, op_id, val_unit, val1, val2 = cond_unit
    if type(val1) is not dict:
        val1 = None
    else:
        val1 = rebuild_sql_val(val1)
    if type(val2) is not dict:
        val2 = None
    else:
        val2 = rebuild_sql_val(val2)
    return not_op, op_id, val_unit, val1, val2


def rebuild_condition_val(condition):
    if condition is None or not DISABLE_VALUE:
        return condition

    res = []
    for idx, it in enumerate(condition):
        if idx % 2 == 0:
            res.append(rebuild_cond_unit_val(it))
        else:
            res.append(it)
    return res


def rebuild_sql_val(sql):
    if sql is None or not DISABLE_VALUE:
        return sql

    sql['from']['conds'] = rebuild_condition_val(sql['from']['conds'])
    sql['having'] = rebuild_condition_val(sql['having'])
    sql['where'] = rebuild_condition_val(sql['where'])
    sql['intersect'] = rebuild_sql_val(sql['intersect'])
    sql['except'] = rebuild_sql_val(sql['except'])
    sql['union'] = rebuild_sql_val(sql['union'])

    return sql


# Rebuild SQL functions for foreign key evaluation
def build_valid_col_units(table_units, schema):
    col_ids = [table_unit[1] for table_unit in table_units if table_unit[0] == TABLE_TYPE['table_unit']]
    prefixs = [col_id[:-2] for col_id in col_ids]
    valid_col_units= []
    for value in schema.idMap.values():
        if '.' in value and value[:value.index('.')] in prefixs:
            valid_col_units.append(value)
    return valid_col_units


def rebuild_col_unit_col(valid_col_units, col_unit, kmap):
    if col_unit is None:
        return col_unit

    agg_id, col_id, distinct = col_unit
    if col_id in kmap and col_id in valid_col_units:
        col_id = kmap[col_id]
    if DISABLE_DISTINCT:
        distinct = None
    return agg_id, col_id, distinct


def rebuild_val_unit_col(valid_col_units, val_unit, kmap):
    if val_unit is None:
        return val_unit

    unit_op, col_unit1, col_unit2 = val_unit
    col_unit1 = rebuild_col_unit_col(valid_col_units, col_unit1, kmap)
    col_unit2 = rebuild_col_unit_col(valid_col_units, col_unit2, kmap)
    return unit_op, col_unit1, col_unit2


def rebuild_table_unit_col(valid_col_units, table_unit, kmap):
    if table_unit is None:
        return table_unit

    table_type, col_unit_or_sql = table_unit
    if isinstance(col_unit_or_sql, tuple):
        col_unit_or_sql = rebuild_col_unit_col(valid_col_units, col_unit_or_sql, kmap)
    return table_type, col_unit_or_sql


def rebuild_cond_unit_col(valid_col_units, cond_unit, kmap):
    if cond_unit is None:
        return cond_unit

    not_op, op_id, val_unit, val1, val2 = cond_unit
    val_unit = rebuild_val_unit_col(valid_col_units, val_unit, kmap)
    return not_op, op_id, val_unit, val1, val2


def rebuild_condition_col(valid_col_units, condition, kmap):
    for idx in range(len(condition)):
        if idx % 2 == 0:
            condition[idx] = rebuild_cond_unit_col(valid_col_units, condition[idx], kmap)
    return condition


def rebuild_select_col(valid_col_units, sel, kmap):
    if sel is None:
        return sel
    distinct, _list = sel
    new_list = []
    for it in _list:
        agg_id, val_unit = it
        new_list.append((agg_id, rebuild_val_unit_col(valid_col_units, val_unit, kmap)))
    if DISABLE_DISTINCT:
        distinct = None
    return distinct, new_list


def rebuild_from_col(valid_col_units, from_, kmap):
    if from_ is None:
        return from_

    from_['table_units'] = [rebuild_table_unit_col(valid_col_units, table_unit, kmap) for table_unit in from_['table_units']]
    from_['conds'] = rebuild_condition_col(valid_col_units, from_['conds'], kmap)
    return from_


def rebuild_group_by_col(valid_col_units, group_by, kmap):
    if group_by is None:
        return group_by

    return [rebuild_col_unit_col(valid_col_units, col_unit, kmap) for col_unit in group_by]


def rebuild_order_by_col(valid_col_units, order_by, kmap):
    if order_by is None or len(order_by) == 0:
        return order_by

    direction, val_units = order_by
    new_val_units = [rebuild_val_unit_col(valid_col_units, val_unit, kmap) for val_unit in val_units]
    return direction, new_val_units


def rebuild_sql_col(valid_col_units, sql, kmap):
    if sql is None:
        return sql

    sql['select'] = rebuild_select_col(valid_col_units, sql['select'], kmap)
    sql['from'] = rebuild_from_col(valid_col_units, sql['from'], kmap)
    sql['where'] = rebuild_condition_col(valid_col_units, sql['where'], kmap)
    sql['groupBy'] = rebuild_group_by_col(valid_col_units, sql['groupBy'], kmap)
    sql['orderBy'] = rebuild_order_by_col(valid_col_units, sql['orderBy'], kmap)
    sql['having'] = rebuild_condition_col(valid_col_units, sql['having'], kmap)
    sql['intersect'] = rebuild_sql_col(valid_col_units, sql['intersect'], kmap)
    sql['except'] = rebuild_sql_col(valid_col_units, sql['except'], kmap)
    sql['union'] = rebuild_sql_col(valid_col_units, sql['union'], kmap)

    return sql


def build_foreign_key_map(entry):
    cols_orig = entry["column_names_original"]
    tables_orig = entry["table_names_original"]

    # rebuild cols corresponding to idmap in Schema
    cols = []
    for col_orig in cols_orig:
        if col_orig[0] >= 0:
            t = tables_orig[col_orig[0]]
            c = col_orig[1]
            cols.append("__" + t.lower() + "." + c.lower() + "__")
        else:
            cols.append("__all__")

    def keyset_in_list(k1, k2, k_list):
        for k_set in k_list:
            if k1 in k_set or k2 in k_set:
                return k_set
        new_k_set = set()
        k_list.append(new_k_set)
        return new_k_set

    foreign_key_list = []
    foreign_keys = entry["foreign_keys"]
    for fkey in foreign_keys:
        key1, key2 = fkey
        key_set = keyset_in_list(key1, key2, foreign_key_list)
        key_set.add(key1)
        key_set.add(key2)

    foreign_key_map = {}
    for key_set in foreign_key_list:
        sorted_list = sorted(list(key_set))
        midx = sorted_list[0]
        for idx in sorted_list:
            foreign_key_map[cols[idx]] = cols[midx]

    return foreign_key_map


def build_foreign_key_map_from_json(table):
    with open(table) as f:
        data = json.load(f)
    tables = {}
    for entry in data:
        tables[entry['db_id']] = build_foreign_key_map(entry)
    return tables


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    
    # 变动1：删除了 --gold 和 --pred，替换为 --input
    parser.add_argument('--input', dest='input', type=str, required=True, 
                        help="path to the input json file containing gold and pred")
    
    parser.add_argument('--db', dest='db', type=str, required=True, 
                        help="the directory that contains all the databases and test suites")
    parser.add_argument('--table', dest='table', type=str, 
                        help="the tables.json schema file")
    parser.add_argument('--etype', dest='etype', type=str, default='exec',
                        help="evaluation type, exec or match",
                        choices=('all', 'exec', 'match'))
    parser.add_argument('--plug_value', default=False, action='store_true',
                        help='whether to plug in the gold value into the predicted query')
    parser.add_argument('--keep_distinct', default=False, action='store_true',
                        help='whether to keep distinct keyword during evaluation')
    parser.add_argument('--progress_bar_for_each_datapoint', default=False, action='store_true',
                        help='whether to print progress bar')
    parser.add_argument('--timeout', dest='timeout', type=int, default=30,
                        help='timeout in seconds for each query execution (default: 30)')
    args = parser.parse_args()

    # 准备 Foreign Key Maps (仅在 match 模式下需要)
    kmaps = None
    if args.etype in ['all', 'match']:
        assert args.table is not None, 'table argument must be non-None if exact set match is evaluated'
        kmaps = build_foreign_key_map_from_json(args.table)

    # 变动2：调用 evaluate 时只传 args.input，不再传 gold 和 pred
    evaluate(args.input, args.db, args.etype, kmaps, args.plug_value, args.keep_distinct, args.progress_bar_for_each_datapoint, args.timeout)


'''
python3 evaluation.py \
    --input /amax/storage/qcjySONG/EHRSQL/EHRSQL_KG/Result/Ministral_T_unique.json \
    --db /amax/storage/qcjySONG/EHRSQL/raw_dataset/EHR_DB \
    --table /amax/storage/qcjySONG/EHRSQL/raw_dataset/EHR_DB/mimic_iv/tables.json \
    --etype exec \
    --progress_bar_for_each_datapoint


python3 evaluation.py \
    --input /amax/storage/nfs/qcjySONG/EHR24/output_json/4B_Close_TEST_SUCCESS_ag_2.json \
    --db /amax/storage/qcjySONG/EHRSQL/raw_dataset/EHR_DB \
    --table /amax/storage/qcjySONG/EHRSQL/raw_dataset/EHR_DB/mimic_iv/tables.json \
    --etype exec \
    --progress_bar_for_each_datapoint



## Nor
4B: 0.484
4B_OSS: 0.593
4B_Close_2: 0.609
4B_Close_2_ag: 0.623 #宣传
4B_Close_2_ag_2: 0.626


                     easy                 medium               hard                 extra                all                 
count                26                   7                    16                   657                  706                 
=====================   EXECUTION ACCURACY     =====================
execution            1.000                0.286                0.750                0.609                0.623  

'''