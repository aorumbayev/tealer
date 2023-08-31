from typing import Type, Tuple
import base64

import pytest

from tealer.teal.instructions import instructions
from tealer.teal.instructions.instructions import (
    IntcInstruction,
    BytecInstruction,
    Callsub,
    Retsub,
)
from tealer.teal.instructions import transaction_field
from tealer.teal.instructions.parse_instruction import parse_line, ParseError
from tealer.teal.parse_teal import parse_teal
from tealer.utils.analyses import is_int_push_ins, is_byte_push_ins
from tealer.exceptions import TealerException

from tests.utils import order_basic_blocks, cmp_instructions, cmp_basic_blocks

TARGETS = [
    "tests/parsing/teal1-instructions.teal",
    "tests/parsing/transaction_fields.teal",
    "tests/parsing/global_fields.teal",
    "tests/parsing/instructions.teal",
    "tests/parsing/instructions.teal",
    "tests/parsing/instructions.teal",
    "tests/parsing/teal3-gload.teal",
    "tests/parsing/teal4-test0.teal",
    "tests/parsing/teal4-test1.teal",
    "tests/parsing/teal4-test2.teal",
    "tests/parsing/teal4-test3.teal",
    "tests/parsing/teal4-test4.teal",
    "tests/parsing/teal4-test5.teal",
    "tests/parsing/teal4-random-opcodes.teal",
    "tests/parsing/comments.teal",
    "tests/parsing/asset_holding_get.teal",
    "tests/parsing/asset_params_get.teal",
    "tests/parsing/teal5-app_params_get.teal",
    "tests/parsing/teal5-ecdsa.teal",
    "tests/parsing/teal5-extract.teal",
    "tests/parsing/teal5-itxn.teal",
    "tests/parsing/teal5-itxna.teal",
    "tests/parsing/teal5-mixed.teal",
    "tests/parsing/teal-instructions-with-versions.teal",
    "tests/parsing/teal-fields-with-versions.teal",
    "tests/parsing/multiple_retsub.teal",
    "tests/parsing/subroutine_jump_back.teal",
    "tests/parsing/teal6-acct_params_get.teal",
    "tests/parsing/teal6-instructions.teal",
    "tests/parsing/teal7-instructions.teal",
    "tests/parsing/teal8-instructions.teal",
]

TEST_CODE = """
intcblock 0xf 017 15
intcblock
int pay
pushint 1
byte base64 AA
byte b64 AA
byte base64(AA)
byte b64(AA)
byte base32 AA
byte b32 AA
byte base32(AA)
byte b32(AA)
byte 0x0123456789abcdef
byte "\x01\x02"
byte "string literal"
bytecblock b32(AA) base64 AA 0x00 "00"
bytecblock
byte "not label: // not comment either"
labelwithqoute": // valid
gtxn 1 Sender
gtxna 1 Applications 0
extract 0 1
gtxnas 1 Applications
gitxn 1 Sender
replace 1
replace2 1
match label1 label2
switch label1 label2
label1:
int 1
label2:
int 2
method "add(uint64)uint64"
"""

invalid_instructions = """
byte "sdf       // missing closing qoute
byte base64     // missing encoded string
byte b64        // missing encoded string
byte base32     // missing encoded string
byte b32        // missing encoded string
byte base64(AA  // missing closing paranthesis
byte b64(AA     // missing closing paranthesis
byte base32(AA  // missing closing paranthesis
byte b32(AA     // missing closing paranthesis
label: add      // contains additional token add
byte "1" "2"    // byte expects only one immediate arg.
byte sdf        // incorrect byte format
"""

unsupported_instructions = """
not an instruction
"""


@pytest.mark.parametrize("target", TARGETS)  # type: ignore
def test_parsing(target: str) -> None:
    with open(target, encoding="utf-8") as f:
        teal = parse_teal(f.read())
    # print instruction to trigger __str__ on each ins
    for i in teal.instructions:
        assert not isinstance(i, instructions.UnsupportedInstruction), f'ins "{i}" is not supported'
        print(i, i.cost)


# test parsing by comparing old CFG and new CFG.
@pytest.mark.parametrize("target", TARGETS)  # type: ignore
def test_parsing_with_reference(target: str) -> None:  # pylint: disable=too-many-locals
    with open(target, encoding="utf-8") as f:
        teal = parse_teal(f.read())

    for i in teal._instructions_NEW:
        assert not isinstance(i, instructions.UnsupportedInstruction), f'ins "{i}" is not supported'
        print(i, i.cost)

    # instructions should be same
    assert len(teal._instructions_NEW) == len(teal.instructions)
    for ins_new, ins_old in zip(teal._instructions_NEW, teal.instructions):
        assert cmp_instructions(ins_new, ins_old)

    # divison of contract into basic blocks should also be same
    assert len(teal._bbs_NEW) == len(teal.bbs)
    bbs_new = order_basic_blocks(teal._bbs_NEW)
    bbs_old = order_basic_blocks(teal.bbs)
    for bb_new, bb_old in zip(bbs_new, bbs_old):
        assert bb_new.idx == bb_old.idx
        assert cmp_basic_blocks(bb_new, bb_old)

    # next blocks of blocks ending with callsub should be different
    # next blocks of blocks ending with retsub should be different
    # Other blocks should have same next blocks
    for bb_new, bb_old in zip(bbs_new, bbs_old):
        if isinstance(bb_new.exit_instr, Retsub):
            # retsub is exit instruction
            assert len(bb_new.next) == 0
            continue
        if isinstance(bb_new.exit_instr, Callsub):
            # callsub has one next block unless it is the last instruction
            assert len(bb_new.next) == 0 or len(bb_new.next) == 1
            if len(bb_new.next) == 0:
                continue
            # return points should be equal
            return_point_block_new = bb_new.next[0]
            assert isinstance(bb_old.exit_instr, Callsub)
            assert bb_old.exit_instr.return_point
            return_point_block_old = bb_old.exit_instr.return_point.bb

            assert return_point_block_new.idx == return_point_block_old.idx
            assert cmp_basic_blocks(return_point_block_new, return_point_block_old)
            continue

        next_new = order_basic_blocks(list(set(bb_new.next)))
        next_old = order_basic_blocks(list(set(bb_old.next)))

        assert len(next_new) == len(next_old)
        for bi, bj in zip(next_new, next_old):
            assert bi.idx == bj.idx
            assert cmp_basic_blocks(bi, bj)


def _cmp_instructions(
    b1: instructions.Instruction,
    b2: instructions.Instruction,
    target: Type[instructions.Instruction],
    attributes: Tuple[str],
) -> bool:
    check = isinstance(b1, target) and isinstance(b2, target)
    if not check:
        return check

    for attr in attributes:
        v1 = getattr(b1, attr, None)
        v2 = getattr(b2, attr, None)
        if v1 != v2:
            return False
    return True


def test_parsing_2() -> None:
    teal = parse_teal(TEST_CODE)
    ins1 = teal.instructions
    ins2 = [
        instructions.Intcblock([15, 15, 15]),
        instructions.Intcblock([]),
        instructions.Int("pay"),
        instructions.PushInt(1),
        instructions.Byte("0x00"),
        instructions.Byte("0x00"),
        instructions.Byte("0x00"),
        instructions.Byte("0x00"),
        instructions.Byte("0x00"),
        instructions.Byte("0x00"),
        instructions.Byte("0x00"),
        instructions.Byte("0x00"),
        instructions.Byte("0x0123456789abcdef"),
        instructions.Byte('"\x01\x02"'),
        instructions.Byte('"string literal"'),
        instructions.Bytecblock(["0x00", "0x00", "0x00", '"00"']),
        instructions.Bytecblock([]),
        instructions.Byte('"not label: // not comment either"'),
        instructions.Label('labelwithqoute"'),
        instructions.Gtxn(1, transaction_field.Sender()),
        instructions.Gtxna(1, transaction_field.Applications(0)),
        instructions.Extract(0, 1),
        instructions.Gtxnas(1, transaction_field.Applications(-1)),
        instructions.Gitxn(1, transaction_field.Sender()),
        instructions.Replace(1),
        instructions.Replace2(1),
        instructions.Match(["label1", "label2"]),
        instructions.Switch(["label1", "label2"]),
        instructions.Label("label1"),
        instructions.Int(1),
        instructions.Label("label2"),
        instructions.Int(2),
        instructions.Method('"add(uint64)uint64"'),
    ]
    t = [
        (instructions.Intcblock, ("_constants",)),
        (instructions.Intcblock, ("_constants",)),
        (instructions.Int, ("value",)),
        (instructions.PushInt, ("value",)),
        (instructions.Byte, ("_bytes",)),
        (instructions.Byte, ("_bytes",)),
        (instructions.Byte, ("_bytes",)),
        (instructions.Byte, ("_bytes",)),
        (instructions.Byte, ("_bytes",)),
        (instructions.Byte, ("_bytes",)),
        (instructions.Byte, ("_bytes",)),
        (instructions.Byte, ("_bytes",)),
        (instructions.Byte, ("_bytes",)),
        (instructions.Byte, ("_bytes",)),
        (instructions.Byte, ("_bytes",)),
        (instructions.Bytecblock, ("_constants",)),
        (instructions.Bytecblock, ("_constants",)),
        (instructions.Byte, ("_bytes",)),
        (instructions.Label, ("label",)),
        (instructions.Gtxn, ("idx",)),
        (instructions.Gtxna, ("idx",)),
        (instructions.Extract, ("start_position", "length")),
        (instructions.Gtxnas, ("idx",)),
        (instructions.Gitxn, ("idx",)),
        (instructions.Replace, ("start_position", "is_replace2", "is_replace3")),
        (instructions.Replace2, ("start_position",)),
        (instructions.Match, ("labels",)),
        (instructions.Switch, ("labels",)),
        (instructions.Label, ("label",)),
        (instructions.Int, ("value",)),
        (instructions.Label, ("label",)),
        (instructions.Int, ("value",)),
        (instructions.Method, ("method_signature",)),
    ]

    attributes: Tuple[str]
    for (b1, b2, (target, attributes)) in zip(ins1, ins2, t):  # type: ignore
        assert _cmp_instructions(b1, b2, target, attributes)


def test_invalid_instructions() -> None:
    for line in invalid_instructions.strip().splitlines():
        with pytest.raises(ParseError):
            parse_line(line)


def test_unsupported_instructions() -> None:
    for line in unsupported_instructions.strip().splitlines():
        ins = parse_line(line)
        assert isinstance(ins, instructions.UnsupportedInstruction)
        assert str(ins) == f"UNSUPPORTED {line.strip()}"
        assert ins.verbatim_line == line.strip()


def test_field_properties() -> None:
    ins = parse_line("gitxnas 1 ApplicationArgs")
    assert (
        isinstance(ins, instructions.Gitxnas)
        and ins.idx == 1
        and isinstance(ins.field, transaction_field.TransactionArrayField)
        and ins.field.idx == -1
    )

    ins = parse_line("gitxna 1 ApplicationArgs 0")
    assert (
        isinstance(ins, instructions.Gitxna)
        and ins.idx == 1
        and isinstance(ins.field, transaction_field.TransactionArrayField)
        and ins.field.idx == 0
    )


def test_instruction_properties() -> None:
    CURRENT_TEST_CODE = """
    int 1 // comment
    int 2
    """
    teal = parse_teal(CURRENT_TEST_CODE)
    ins1 = teal.instructions[0]
    ins2 = teal.instructions[1]
    assert ins1.prev == []
    assert ins1.next == [ins2]
    assert ins2.prev == [ins1]
    assert ins2.next == []
    assert ins1.comment == "// comment"

    # cannot set return point of callsub instruction multiple times
    ins = parse_line("callsub main")
    assert isinstance(ins, instructions.Callsub) and ins.return_point is None

    ins.return_point = parse_line("int 1")
    with pytest.raises(Exception):
        ins.return_point = parse_line("int 1")  # cannot set multiple times

    # accessing replace instruction start_position fails if it is None i.e if there's no immediate argument.
    # it should be checked that whether given replace instruction is semantically equivalent to replace2 or replace3.
    ins = parse_line("replace 1")
    assert isinstance(ins, instructions.Replace) and ins.is_replace2 and ins.start_position == 1

    ins = parse_line("replace")
    assert isinstance(ins, instructions.Replace) and ins.is_replace3
    with pytest.raises(Exception):
        print(ins.start_position)


def test_cost_values() -> None:
    CURRENT_TEST_CODE = """
    sha256
    sha512_256
    keccak256
    ecdsa_verify Secp256k1
    ecdsa_pk_decompress Secp256k1
    ecdsa_pk_recover Secp256k1
    b%
    b&
    b|
    b+
    b-
    b/
    b*
    b^
    b~
    divmodw
    expw
    sqrt
    bsqrt
    base64_decode URLEncoding
    json_ref JSONUint64
    ed25519verify_bare
    vrf_verify VrfAlgorand
    """
    for line in CURRENT_TEST_CODE.strip().splitlines():
        # when cost parameter accessed, it checks that instruction object's BasicBlock is not none. if it is none, then
        # cost property raises exception. These tests are included to cover those brances.
        with pytest.raises(TealerException):
            print(line)
            # pylint: disable=expression-not-assigned
            parse_line(line).cost  # type: ignore

    # cost should return 0 if the contract version is less than instruction supported version
    CURRENT_TEST_CODE = """
    ecdsa_verify Secp256k1
    ecdsa_pk_decompress Secp256k1
    ecdsa_pk_recover Secp256k1
    bsqrt
    base64_decode URLEncoding
    json_ref JSONUint64
    ed25519verify_bare
    """
    teal = parse_teal(CURRENT_TEST_CODE)
    for ins in teal.instructions:
        print("DSDF", ins, ins.cost)
        assert ins.cost == 0


INTCBLOCK_TESTS = [
    "tests/parsing/intcblock_1.teal",
    "tests/parsing/intcblock_2.teal",
    "tests/parsing/intcblock_3.teal",
]


@pytest.mark.parametrize("test", INTCBLOCK_TESTS)  # type: ignore
def test_intc_bytec(test: str) -> None:
    with open(test, encoding="utf-8") as f:
        teal = parse_teal(f.read())

    for ins in teal.instructions:
        if isinstance(ins, IntcInstruction):
            is_known, value = is_int_push_ins(ins)
            assert is_known
            assert value == int(ins.comment[2:].strip())

        if isinstance(ins, BytecInstruction):
            is_known, value = is_byte_push_ins(ins)
            assert is_known
            expected_value = ins.comment[2:].strip()
            if expected_value.startswith('"'):
                expected_value = "0x" + expected_value[1:-1].encode().hex()
            elif expected_value.startswith("addr"):
                s = expected_value.strip("addr ").strip()
                expected_value = (
                    "0x" + base64.b32decode(s + "=" * (-len(s) % 8))[:-4].hex()
                )  # 4-byte checksum
            assert value == expected_value


INTCBLOCK_FALSE_TEST_1 = """
#pragma version 7
intcblock 0x00 0x01 0x02 0x03 0x04 0x05 0x06
intcblock 0x07 0x08 0x09 0x10 0x11 0x12 0x13
intc0
intc1
intc2
intc3
intc 4
return
"""

INTCBLOCK_FALSE_TEST_2 = """
#pragma version 7
int 0
int 1
+
b next
next:
intcblock 0x00 0x01 0x02 0x03 0x04 0x05 0x06
intc0
intc1
intc2
intc3
intc 4
return
"""

INTCBLOCK_FALSE_TEST_3 = """
#pragma version 7
intc0
intc1
return
"""

# Tealer can determine that this is invalid code in this case. But currently does
# not do that to be uniform for all cases.
INTCBLOCK_FALSE_TEST_4 = """
#pragma version 7
intcblock 0x00 0x01 0x02 0x03
intc 7  // Invalid index
"""

# Tealer cannot determing in this case.
INTCBLOCK_FALSE_TEST_5 = """
#pragma version 7
intcblock 0x00 0x01 0x02 0x03
txn RekeyTo
global ZeroAddress
==
bnz branch
b sub
branch:
    intcblock 0x04 0x05 0x06 0x07 0x08 0x09 0xa
sub:
    intc 9 // Invalid index
"""

INTCBLOCK_FALSE_TESTS = [
    INTCBLOCK_FALSE_TEST_1,
    INTCBLOCK_FALSE_TEST_2,
    INTCBLOCK_FALSE_TEST_3,
    INTCBLOCK_FALSE_TEST_4,
    INTCBLOCK_FALSE_TEST_5,
]


@pytest.mark.parametrize("test", INTCBLOCK_FALSE_TESTS)  # type: ignore
def test_intc_bytec_false(test: str) -> None:
    teal = parse_teal(test)
    for ins in teal.instructions:
        if isinstance(ins, IntcInstruction):
            is_int, value = is_int_push_ins(ins)
            assert is_int and value is None

    teal = parse_teal(test.replace("intc", "bytec"))
    for ins in teal.instructions:
        if isinstance(ins, BytecInstruction):
            is_bytes, value = is_byte_push_ins(ins)
            assert is_bytes and value is None
