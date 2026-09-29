"""Дочерний процесс гонки в sp_cpsat.py: решить MIP из MPS-файла HiGHS (1 поток) и записать ответ в JSON.
Usage: highs_mip.py <model.mps> <time_limit> <objective_bound|none> <out.json>"""
import json, sys
import highspy

mps, tl, ob, out = sys.argv[1], float(sys.argv[2]), sys.argv[3], sys.argv[4]
h = highspy.Highs(); h.setOptionValue('output_flag', False); h.setOptionValue('threads', 1)
h.readModel(mps)
h.setOptionValue('time_limit', tl); h.setOptionValue('mip_rel_gap', 0.0)
if ob != 'none': h.setOptionValue('objective_bound', float(ob))
h.run(); info = h.getInfo(); st = str(h.getModelStatus())
res = dict(status=st, primal=info.primal_solution_status == 2, obj=info.objective_function_value, bound=info.mip_dual_bound)
if res['primal']: res['x'] = list(h.getSolution().col_value)
json.dump(res, open(out, 'w'))
