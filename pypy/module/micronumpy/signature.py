from pypy.rlib.objectmodel import r_dict, compute_identity_hash
from pypy.module.micronumpy.interp_iter import ViewIterator, ArrayIterator, \
     BroadcastIterator, OneDimIterator, ConstantIterator
from pypy.rlib.jit import hint, unroll_safe

# def components_eq(lhs, rhs):
#     if len(lhs) != len(rhs):
#         return False
#     for i in range(len(lhs)):
#         v1, v2 = lhs[i], rhs[i]
#         if type(v1) is not type(v2) or not v1.eq(v2):
#             return False
#     return True

# def components_hash(components):
#     res = 0x345678
#     for component in components:
#         res = intmask((1000003 * res) ^ component.hash())
#     return res

def sigeq(one, two):
    return one.eq(two)

def sighash(sig):
    return sig.hash()

known_sigs = r_dict(sigeq, sighash)

def find_sig(sig):
    try:
        return known_sigs[sig]
    except KeyError:
        sig.invent_numbering()
        known_sigs[sig] = sig
        return sig

class NumpyEvalFrame(object):
    _virtualizable2_ = ['iterators[*]', 'final_iter']

    def __init__(self, iterators):
        self = hint(self, access_directly=True)
        self.iterators = iterators
        for i, iter in enumerate(self.iterators):
            if not isinstance(iter, ConstantIterator):# or not isinstance(iter, BroadcastIterator):
                self.final_iter = i
                break
        else:
            raise Exception("Cannot find a non-broadcast non-constant iter")

    def done(self):
        return self.iterators[self.final_iter].done()

    @unroll_safe
    def next(self, shapelen):
        for i in range(len(self.iterators)):
            self.iterators[i] = self.iterators[i].next(shapelen)

class Signature(object):
    def invent_numbering(self):
        cache = r_dict(sigeq, sighash)
        allnumbers = []
        self._invent_numbering(cache, allnumbers)

    def _invent_numbering(self, cache, allnumbers):
        try:
            no = cache[self]
        except KeyError:
            no = len(allnumbers)
            cache[self] = no
            allnumbers.append(no)
        self.iter_no = no

    def create_frame(self, arr):
        iterlist = []
        self._create_iter(iterlist, arr)
        return NumpyEvalFrame(iterlist)

class ConcreteSignature(Signature):
    def __init__(self, dtype):
        self.dtype = dtype

    def eq(self, other):
        if type(self) is not type(other):
            return False
        return self.dtype is other.dtype

    def hash(self):
        return compute_identity_hash(self.dtype)

class ArraySignature(ConcreteSignature):
    def debug_repr(self):
        return 'Array'

    def _create_iter(self, iterlist, arr):
        from pypy.module.micronumpy.interp_numarray import W_NDimArray
        arr = arr.get_concrete()
        assert isinstance(arr, W_NDimArray)
        if self.iter_no >= len(iterlist):
            iterlist.append(ArrayIterator(arr.size))

    def eval(self, frame, arr):
        from pypy.module.micronumpy.interp_numarray import W_NDimArray
        arr = arr.get_concrete()
        assert isinstance(arr, W_NDimArray)
        iter = frame.iterators[self.iter_no]
        return arr.dtype.getitem(arr.storage, iter.offset)

class ScalarSignature(ConcreteSignature):
    def debug_repr(self):
        return 'Scalar'

    def _create_iter(self, iterlist, arr):
        if self.iter_no >= len(iterlist):
            iter = ConstantIterator()
            iterlist.append(iter)

    def eval(self, frame, arr):
        from pypy.module.micronumpy.interp_numarray import Scalar
        assert isinstance(arr, Scalar)
        return arr.value

class ViewSignature(Signature):
    def __init__(self, child):
        self.child = child
    
    def eq(self, other):
        if type(self) is not type(other):
            return False
        return self.child.eq(other.child)

    def hash(self):
        return self.child.hash() ^ 0x12345

    def debug_repr(self):
        return 'Slice(%s)' % self.child.debug_repr()

    def _invent_numbering(self, cache, allnumbers):
        # always invent a new number for view
        no = len(allnumbers)
        allnumbers.append(no)
        self.iter_no = no

    def _create_iter(self, iterlist, arr):
        if self.iter_no >= len(iterlist):
            iterlist.append(ViewIterator(arr))

    def eval(self, frame, arr):
        from pypy.module.micronumpy.interp_numarray import W_NDimSlice
        assert isinstance(arr, W_NDimSlice)
        arr = arr.get_concrete()
        iter = frame.iterators[self.iter_no]
        return arr.find_dtype().getitem(arr.parent.storage, iter.offset)

class FlatiterSignature(ViewSignature):
    def debug_repr(self):
        return 'FlatIter(%s)' % self.child.debug_repr()

    def _create_iter(self, iterlist, arr):
        raise NotImplementedError

class Call1(Signature):
    def __init__(self, func, child):
        self.unfunc = func
        self.child = child

    def hash(self):
        return compute_identity_hash(self.unfunc) ^ self.child.hash() << 1

    def eq(self, other):
        if type(self) is not type(other):
            return False
        return self.unfunc is other.unfunc and self.child.eq(other.child)

    def debug_repr(self):
        return 'Call1(%s)' % (self.child.debug_repr())

    def _invent_numbering(self, cache, allnumbers):
        self.child._invent_numbering(cache, allnumbers)

    def _create_iter(self, iterlist, arr):
        from pypy.module.micronumpy.interp_numarray import Call1
        assert isinstance(arr, Call1)
        self.child._create_iter(iterlist, arr.values)

    def eval(self, frame, arr):
        from pypy.module.micronumpy.interp_numarray import Call1
        assert isinstance(arr, Call1)
        v = self.child.eval(frame, arr.values).convert_to(arr.res_dtype)
        return self.unfunc(arr.res_dtype, v)

class Call2(Signature):
    def __init__(self, func, left, right):
        self.binfunc = func
        self.left = left
        self.right = right

    def hash(self):
        return (compute_identity_hash(self.binfunc) ^ (self.left.hash() << 1) ^
                (self.right.hash() << 2))

    def eq(self, other):
        if type(self) is not type(other):
            return False
        return (self.binfunc is other.binfunc and
                self.left.eq(other.left) and self.right.eq(other.right))

    def _invent_numbering(self, cache, allnumbers):
        self.left._invent_numbering(cache, allnumbers)
        self.right._invent_numbering(cache, allnumbers)

    def _create_iter(self, iterlist, arr):
        from pypy.module.micronumpy.interp_numarray import Call2
        
        assert isinstance(arr, Call2)
        self.left._create_iter(iterlist, arr.left)
        self.right._create_iter(iterlist, arr.right)

    def eval(self, frame, arr):
        from pypy.module.micronumpy.interp_numarray import Call2
        assert isinstance(arr, Call2)
        lhs = self.left.eval(frame, arr.left).convert_to(arr.calc_dtype)
        rhs = self.right.eval(frame, arr.right).convert_to(arr.calc_dtype)
        return self.binfunc(arr.calc_dtype, lhs, rhs)

    def debug_repr(self):
        return 'Call2(%s, %s)' % (self.left.debug_repr(),
                                  self.right.debug_repr())

class ReduceSignature(Call2):
    def _create_iter(self, iterlist, arr):
        self.right._create_iter(iterlist, arr)

    def _invent_numbering(self, cache, allnumbers):
        self.right._invent_numbering(cache, allnumbers)

    def eval(self, frame, arr):
        return self.right.eval(frame, arr)
