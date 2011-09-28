from pypy.rlib.objectmodel import instantiate
from pypy.jit.backend.ppc.ppcgen.locations import (imm, RegisterLocation,
                                                   ImmLocation, StackLocation)
from pypy.jit.backend.ppc.ppcgen.register import *
from pypy.jit.backend.ppc.ppcgen.codebuilder import hi, lo
from pypy.jit.backend.ppc.ppcgen.ppc_assembler import AssemblerPPC
from pypy.jit.backend.ppc.ppcgen.arch import WORD

class MockBuilder(object):
    
    def __init__(self):
        self.reset()

    def __getattr__(self, name):
        instr = MockInstruction(name)
        self.instrs.append(instr)
        return instr

    def str_instrs(self):
        return [str(inst) for inst in self.instrs]

    def reset(self):
        self.instrs = []

class MockInstruction(object):

    def __init__(self, name, *args):
        self.name = name
        self.args = args

    def __call__(self, *args):
        self.args = args

    def __eq__(self, other):
        assert isinstance(other, MockInstruction)
        return self.name == other.name and self.args == other.args

    def __repr__(self):
        return self.__str__()

    def __str__(self):
        return "%s %r" % (self.name, self.args)
    

MI = MockInstruction

class TestMocks(object):
    
    def setup_method(self, method):
        self.builder = MockBuilder()

    def test_cmp_instruction(self):
        assert MI("a", 1, 2) == MI("a", 1, 2)
        assert not MI("a", 1, 2) == MI("b", 1, 2)
        assert not MI("a", 1, 2) == MI("a", 2, 2)
        assert not MI("a", 1) == MI("a", 1, 2)
        assert not MI("a", 1, 2) == MI("a")
        assert MI("a") == MI("a")

    def test_basic(self):
        exp_instrs = [MI("mr", 3, 5), 
                      MI("foobar"),
                      MI("li", 3, 2),
                      MI("stw", 3, 5, 100)]

        self.builder.mr(3, 5)
        self.builder.foobar()
        self.builder.li(3, 2)
        self.builder.stw(3, 5, 100)
       
        assert self.builder.instrs == exp_instrs
        
        self.builder.blub()
        assert self.builder.instr != exp_instrs

class TestRegallocMov(object):
    
    def setup_method(self, method):
        self.builder = MockBuilder()
        self.asm = instantiate(AssemblerPPC)
        self.asm.mc = self.builder

    def test_immediate_to_reg(self):
        self.asm.regalloc_mov(imm(5), reg(10))
        big = 2 << 28
        self.asm.regalloc_mov(imm(big), reg(0))

        exp_instr = [MI("load_imm", 10, 5), 
                     MI("load_imm", 0, big)]
        assert self.asm.mc.instrs == exp_instr

    def test_immediate_to_mem(self):
        self.asm.regalloc_mov(imm(5), stack(6))
        big = 2 << 28
        self.asm.regalloc_mov(imm(big), stack(7))

        exp_instr = [MI("load_imm", 0, 5),
                     MI("stw", r0.value, SPP.value, -(6 * WORD + WORD)),
                     MI("load_imm", 0, big),
                     MI("stw", r0.value, SPP.value, -(7 * WORD + WORD))]
        assert self.asm.mc.instrs == exp_instr

    def test_mem_to_reg(self):
        self.asm.regalloc_mov(stack(5), reg(10))
        self.asm.regalloc_mov(stack(0), reg(0))
        exp_instrs = [MI("lwz", r10.value, SPP.value, -(5 * WORD + WORD)),
                      MI("lwz", r0.value, SPP.value, -(WORD))]
        assert self.asm.mc.instrs == exp_instrs

    def test_mem_to_mem(self):
        self.asm.regalloc_mov(stack(5), stack(6))
        exp_instrs = [MI("lwz", r0.value, SPP.value, -(5 * WORD + WORD)),
                      MI("stw", r0.value, SPP.value, -(6 * WORD + WORD))]
        assert self.asm.mc.instrs == exp_instrs

    def test_reg_to_reg(self):
        self.asm.regalloc_mov(reg(0), reg(1))
        self.asm.regalloc_mov(reg(5), reg(10))
        exp_instrs = [MI("mr", r1.value, r0.value),
                      MI("mr", r10.value, r5.value)]
        assert self.asm.mc.instrs == exp_instrs

    def test_reg_to_mem(self):
        self.asm.regalloc_mov(reg(5), stack(10))
        self.asm.regalloc_mov(reg(0), stack(2))
        exp_instrs = [MI("stw", r5.value, SPP.value, -(10 * WORD + WORD)),
                      MI("stw", r0.value, SPP.value, -(2 * WORD + WORD))]
        assert self.asm.mc.instrs == exp_instrs

def reg(i):
    return RegisterLocation(i)

def stack(i):
    return StackLocation(i)
