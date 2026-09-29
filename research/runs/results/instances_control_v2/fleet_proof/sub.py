"""подзадача: заявки из списка (индексы), те же бригады и матрицы"""
import sys, numpy as np
src, dst = sys.argv[1], sys.argv[2]; keep = list(map(int, sys.argv[3].split(',')))
L = open(src).read().split('\n'); N, V, S = map(int, L[1].split()); M = S + N
tok = ' '.join(L[2 + N + V:]).split()
out = [L[0] + '_sub', f'{len(keep)} {V} {S}'] + [L[2 + k] for k in keep] + L[2 + N:2 + N + V]
idx = list(range(S)) + [S + k for k in keep]; it = 0
for m in range(8):
    if it >= len(tok): break
    A = np.array(tok[it:it + M * M], dtype=object).reshape(M, M); it += M * M
    out += [' '.join(r) for r in A[np.ix_(idx, idx)]]
open(dst, 'w').write('\n'.join(out) + '\n')
