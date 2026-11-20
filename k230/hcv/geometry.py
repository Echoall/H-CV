def fit_projective(s_values, u_values):
    a = [[float(s), 1.0, -float(u) * float(s)] for s, u in zip(s_values, u_values)]
    b = [float(u) for u in u_values]
    for pivot in range(3):
        largest = pivot
        for row in range(pivot + 1, 3):
            if abs(a[row][pivot]) > abs(a[largest][pivot]):
                largest = row
        if largest != pivot:
            a[pivot], a[largest] = a[largest], a[pivot]
            b[pivot], b[largest] = b[largest], b[pivot]
        divisor = a[pivot][pivot]
        for column in range(pivot, 3):
            a[pivot][column] /= divisor
        b[pivot] /= divisor
        for row in range(3):
            if row == pivot:
                continue
            factor = a[row][pivot]
            for column in range(pivot, 3):
                a[row][column] -= factor * a[pivot][column]
            b[row] -= factor * b[pivot]
    return b[0], b[1], b[2]


def apply_projective(s_value, coefficients):
    a, b, c = coefficients
    return (a * s_value + b) / (c * s_value + 1.0)
