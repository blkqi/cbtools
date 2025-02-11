from functools import *
from itertools import *


def compose(*functions):
    return reduce(lambda f, g: lambda x: f(g(x)), functions)
