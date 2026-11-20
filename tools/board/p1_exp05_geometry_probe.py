import sys

sys.path.append("/sdcard/hcv")
from geometry import apply_projective, fit_projective

s_values = (-110.0, 0.0, 110.0)
u_values = (145.0, 320.0, 495.0)
coefficients = fit_projective(s_values, u_values)
print("HCV_GEOMETRY_COEFFS,%r,%r,%r" % coefficients)
for value in (-135.0, -100.0, -50.0, 0.0, 50.0, 100.0, 135.0):
    print("HCV_GEOMETRY,%r,%r" % (value, apply_projective(value, coefficients)))
