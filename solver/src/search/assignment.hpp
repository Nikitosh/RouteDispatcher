// Задача о назначениях (венгерский алгоритм, O(n²m)): n строк на m ≥ n столбцов, минимум суммы стоимостей.
#pragma once
#include <vector>

namespace dispatch {

// cost[i][j], i < n, j < m. Возвращает столбец для каждой строки.
inline std::vector<int> assignMinCost(const std::vector<std::vector<double>>& cost, int n, int m) {
    const double INF = 1e18;
    std::vector<double> u(n + 1, 0), w(m + 1, 0);
    std::vector<int> match(m + 1, 0), way(m + 1, 0);           // match[j] — строка (с единицы), занявшая столбец j
    for (int i = 1; i <= n; i++) {
        match[0] = i;
        int j0 = 0;
        std::vector<double> minv(m + 1, INF);
        std::vector<char> used(m + 1, 0);
        do {
            used[j0] = 1;
            int i0 = match[j0], j1 = 0;
            double delta = INF;
            for (int j = 1; j <= m; j++) {
                if (used[j]) continue;
                double cur = cost[i0 - 1][j - 1] - u[i0] - w[j];
                if (cur < minv[j]) {
                    minv[j] = cur;
                    way[j] = j0;
                }
                if (minv[j] < delta) {
                    delta = minv[j];
                    j1 = j;
                }
            }
            for (int j = 0; j <= m; j++) {
                if (used[j]) {
                    u[match[j]] += delta;
                    w[j] -= delta;
                } else {
                    minv[j] -= delta;
                }
            }
            j0 = j1;
        } while (match[j0] != 0);
        do {
            int j1 = way[j0];
            match[j0] = match[j1];
            j0 = j1;
        } while (j0);
    }
    std::vector<int> column(n, -1);
    for (int j = 1; j <= m; j++)
        if (match[j]) column[match[j] - 1] = j - 1;
    return column;
}

}  // namespace dispatch
