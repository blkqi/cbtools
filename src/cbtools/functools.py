from functools import reduce, partial
from itertools import chain, groupby
from operator import itemgetter


def compose(*functions):
    return reduce(lambda f, g: lambda x: f(g(x)), functions)


def unique(iterable):
    return map(itemgetter(0), groupby(iterable))


def unique_count(iterable):
    return ((x, sum(1 for _ in g)) for x, g in groupby(iterable))


def not_unique(iterable):
    return (x for x, n in unique_count(iterable) if n > 1)
