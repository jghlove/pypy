from rpython.rtyper.lltypesystem import lltype, rffi
from rpython.rlib.rarithmetic import intmask
from rpython.rlib.objectmodel import we_are_translated
from rpython.rlib.debug import debug_start, debug_print, debug_stop
from rpython.rlib.debug import have_debug_prints
from rpython.jit.backend.llsupport.asmmemmgr import BlockBuilderMixin
from rpython.jit.backend.x86.rx86 import X86_32_CodeBuilder, X86_64_CodeBuilder
from rpython.jit.backend.x86.regloc import LocationCodeBuilder
from rpython.jit.backend.x86.arch import IS_X86_32, IS_X86_64, WORD
from rpython.jit.backend.x86 import valgrind

# XXX: Seems nasty to change the superclass of MachineCodeBlockWrapper
# like this
if IS_X86_32:
    codebuilder_cls = X86_32_CodeBuilder
    backend_name = 'x86'
elif IS_X86_64:
    codebuilder_cls = X86_64_CodeBuilder
    backend_name = 'x86_64'


class MachineCodeBlockWrapper(BlockBuilderMixin,
                              LocationCodeBuilder,
                              codebuilder_cls):
    def __init__(self, cpu):
        self.stm = cpu.gc_ll_descr.stm
        self.init_block_builder()
        # a list of relative positions; for each position p, the bytes
        # at [p-4:p] encode an absolute address that will need to be
        # made relative.  Only works on 32-bit!
        if WORD == 4:
            self.relocations = []
        else:
            self.relocations = None
        #
        # ResOperation --> offset in the assembly.
        # ops_offset[None] represents the beginning of the code after the last op
        # (i.e., the tail of the loop)
        self.ops_offset = {}

    def add_pending_relocation(self):
        self.relocations.append(self.get_relative_pos())

    def mark_op(self, op):
        pos = self.get_relative_pos()
        self.ops_offset[op] = pos

    def copy_to_raw_memory(self, addr):
        self._copy_to_raw_memory(addr)
        if self.relocations is not None:
            for reloc in self.relocations:
                p = addr + reloc
                adr = rffi.cast(rffi.LONGP, p - WORD)
                adr[0] = intmask(adr[0] - p)
        valgrind.discard_translations(addr, self.get_relative_pos())
        self._dump(addr, "jit-backend-dump", backend_name)

    def in_tl_segment(self, adr):
        """Makes 'adr' relative to threadlocal-base if we run in STM. 
        Before using such a relative address, call SEGTL()."""
        if self.stm and we_are_translated():
            # only for STM and not during tests
            from rpython.jit.backend.x86 import stmtlocal, rx86
            result = adr - stmtlocal.threadlocal_base()
            assert rx86.fits_in_32bits(result)
            return result
        return adr

    def SEGTL(self):
        """Insert segment prefix for thread-local memory if we run
        in STM and not during testing.  This is used to access thread-local
        data structures like the struct stm_thread_local_s."""
        if self.stm and we_are_translated():
            from rpython.jit.backend.x86 import stmtlocal
            stmtlocal.tl_segment_prefix(self)

    def SEGC7(self):
        """Insert segment prefix for the stmgc-c7 segment of memory
        if we run in STM and not during testing.  This is used to access
        any GC object, or things in the STM_SEGMENT structure."""
        if self.stm and we_are_translated():
            from rpython.jit.backend.x86 import stmtlocal
            stmtlocal.c7_segment_prefix(self)

    def SEGC7_if_gc(self, op):
        if self.stm and we_are_translated():
            from rpython.jit.backend.x86 import stmtlocal
            from rpython.jit.metainterp.resoperation import rop
            #
            opnum = op.getopnum()
            if opnum in (rop.GETFIELD_GC,
                         rop.GETFIELD_GC_PURE,
                         rop.GETARRAYITEM_GC,
                         rop.GETARRAYITEM_GC_PURE,
                         rop.GETINTERIORFIELD_GC,
                         rop.SETFIELD_GC,
                         rop.SETARRAYITEM_GC,
                         rop.SETINTERIORFIELD_GC,
                         ):
                stmtlocal.c7_segment_prefix(self)
