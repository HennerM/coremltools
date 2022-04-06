#  Copyright (c) 2021, Apple Inc. All rights reserved.
#
#  Use of this source code is governed by a BSD-3-clause license that can be
#  found in the LICENSE.txt file or at https://opensource.org/licenses/BSD-3-Clause

import numpy as np

from coremltools.converters.mil.mil.passes.pass_registry import register_pass
from coremltools.converters.mil.mil.passes.graph_pass import AbstractGraphPass

def _remove_elementwise_binary(op, block, x, y):
    # We remove the ops that has op.x == x or op.y == y
    if x is not None and op.x.val is not None and np.all(op.x.val == x):
        input_var = op.y
        input_op = input_var.op
    elif y is not None and op.y.val is not None and np.all(op.y.val == y):
        input_var = op.x
        input_op = input_var.op
    else:
        return False

    input_shape = input_var.sym_type
    output_shape = op.outputs[0].sym_type

    # We might be using elementwise as broadcasting
    if input_shape != output_shape:
        return False

    op.enclosing_block.replace_uses_of_var_after_op(
        anchor_op=input_op, old_var=op.outputs[0], new_var=input_var
    )
    block.remove_ops([op])

    return True

def remove_elementwise(op, block):

    if op.op_type in {"add"}:
        return _remove_elementwise_binary(op, block, 0, 0)
    elif op.op_type in {"mul"}:
        return _remove_elementwise_binary(op, block, 1, 1)
    elif op.op_type in {"floor_div", "pow", "real_div"}:
        return _remove_elementwise_binary(op, block, None, 1)
    elif op.op_type in {"sub"}:
        return _remove_elementwise_binary(op, block, None, 0)
    else:
        return False

def remove_same_shape(op, block):
    input_shape = op.x.sym_type
    output_shape = op.outputs[0].sym_type

    if input_shape != output_shape:
        return False

    input_var = op.x
    input_op = input_var.op

    op.enclosing_block.replace_uses_of_var_after_op(
        anchor_op=input_op, old_var=op.outputs[0], new_var=input_var
    )

    # Remove all the ops at once
    block.remove_ops([op])
    return True

def remove_linear(op, block):
    if op.alpha.val != 1 or op.beta.val != 0:
        return False

    input_var = op.x
    input_op = input_var.op

    op.enclosing_block.replace_uses_of_var_after_op(
        anchor_op=input_op, old_var=op.outputs[0], new_var=input_var
    )

    # Remove all the ops at once
    block.remove_ops([op])
    return True

def remove_transpose(op, block):
    perm = np.array([p if p >= 0 else p+len(op.perm.val) for p in op.perm.val])
    sorted_perm = np.sort(perm)
    if (perm != sorted_perm).any():
        return False

    input_var = op.x
    input_op = input_var.op

    op.enclosing_block.replace_uses_of_var_after_op(
        anchor_op=input_op, old_var=op.outputs[0], new_var=input_var
    )

    # Remove all the ops at once
    block.remove_ops([op])
    return True
_SUPPORTED_OPS = {
    "add",
    "mul",
    "floor_div",
    "pow",
    "real_div",
    "sub",
    "reshape",
    "split",
    "slice_by_index",
    "slice_by_size",
    "pad",
    "tile",
    "transpose",
    "upsample_nearest_neighbor",
    "upsample_bilinear",
    "resize_bilinear",
    "crop",
    "linear_activation"
}

op_to_removal_fn = {
    "add": remove_elementwise,
    "mul": remove_elementwise,
    "floor_div": remove_elementwise,
    "pow": remove_elementwise,
    "real_div": remove_elementwise,
    "sub": remove_elementwise,
    "reshape": remove_same_shape,
    "split": remove_same_shape,
    "slice_by_index": remove_same_shape,
    "slice_by_size": remove_same_shape,
    "pad": remove_same_shape,
    "tile": remove_same_shape,
    "transpose": remove_transpose,
    "upsample_nearest_neighbor": remove_same_shape,
    "upsample_bilinear": remove_same_shape,
    "resize_bilinear": remove_same_shape,
    "crop": remove_same_shape,
    "linear_activation": remove_linear,
}

def _match_pattern(op):
    # abort if op output is a block output
    if op.outputs[0] in op.enclosing_block.outputs:
        return None

    if op.op_type in _SUPPORTED_OPS:

        if len(op.outputs) != 1:
            return None
        return op_to_removal_fn[op.op_type]

    return None

@register_pass(namespace="common")
class noop_elimination(AbstractGraphPass):
    """
    We remove ops that has no effect.

    Given:
        %1 (1, 96, 128, 64, fp32) = ...
        %2 (1, 96, 128, 64, fp32) = reshape(%1)
        ...
        %3 (1, 96, 128, 64, fp32) = add(%2, constant)
        ...

    Result:
        %1 (1, 96, 128, 64, fp32) = ...
        %3 (1, 96, 128, 64, fp32) = add(%1, constant)
        ...

    """
    def __init__(self):
        self.ops_to_skip = set()

    def set_ops_to_skip(self, prog):
        pass

    def apply(self, prog):
        self.set_ops_to_skip(prog)
        for f in prog.functions.values():
            block_changed = True
            while block_changed:
                block_changed = self._noop_elimination_block(f)

    def _noop_elimination_block(self, block):
        for op in list(block.operations):
            for b in op.blocks:
                block_changed = True
                while block_changed:
                    block_changed = self._noop_elimination_block(b)
            if len(op.blocks) > 0:
                continue

            remove_fn = _match_pattern(op)
            if remove_fn is not None and op not in self.ops_to_skip:
                with block:
                    status = remove_fn(op, block)
                # has to break as the downstream iterator is affected.
                if status:
                    return status
        return False