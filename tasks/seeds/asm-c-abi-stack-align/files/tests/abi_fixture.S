	.data
	.balign 8
	.globl abi_call_count
	.type abi_call_count, @object
	.size abi_call_count, 8
abi_call_count:
	.quad 0

	.globl abi_bad_alignment
	.type abi_bad_alignment, @object
	.size abi_bad_alignment, 8
abi_bad_alignment:
	.quad 0

	.globl abi_trace_hash
	.type abi_trace_hash, @object
	.size abi_trace_hash, 8
abi_trace_hash:
	.quad 0

	.text
	.globl abi_transform
	.type abi_transform, @function
abi_transform:
	.cfi_startproc
	addq $1, abi_call_count(%rip)
	movq %rsp, %rax
	andl $15, %eax
	cmpl $8, %eax
	je .Ltransform_aligned
	addq $1, abi_bad_alignment(%rip)
.Ltransform_aligned:
	movq abi_trace_hash(%rip), %r8
	imulq $257, %r8, %r8
	movq %rdi, %r9
	movq %rsi, %r10
	shlq $32, %r10
	xorq %r10, %r9
	movq %rdx, %r10
	rolq $17, %r10
	xorq %r10, %r9
	addq %r9, %r8
	movq %r8, abi_trace_hash(%rip)

	leaq (%rdi,%rdi,2), %rax
	leaq (%rax,%rsi,4), %rax
	addq %rsi, %rax
	subq %rdx, %rax

	/* Poison all caller-saved integer registers other than the result. */
	movabsq $0x0102030405060708, %rcx
	movabsq $0x1112131415161718, %rdx
	movabsq $0x2122232425262728, %rsi
	movabsq $0x3132333435363738, %rdi
	movabsq $0x4142434445464748, %r8
	movabsq $0x5152535455565758, %r9
	movabsq $0x6162636465666768, %r10
	movabsq $0x7172737475767778, %r11
	ret
	.cfi_endproc
	.size abi_transform, .-abi_transform

	.globl call_with_nonvolatile_sentinels
	.type call_with_nonvolatile_sentinels, @function
call_with_nonvolatile_sentinels:
	.cfi_startproc
	pushq %rbp
	.cfi_def_cfa_offset 16
	.cfi_offset %rbp, -16
	pushq %rbx
	.cfi_def_cfa_offset 24
	.cfi_offset %rbx, -24
	pushq %r12
	.cfi_def_cfa_offset 32
	.cfi_offset %r12, -32
	pushq %r13
	.cfi_def_cfa_offset 40
	.cfi_offset %r13, -40
	pushq %r14
	.cfi_def_cfa_offset 48
	.cfi_offset %r14, -48
	pushq %r15
	.cfi_def_cfa_offset 56
	.cfi_offset %r15, -56
	subq $8, %rsp
	.cfi_def_cfa_offset 64
	movq %rcx, (%rsp)

	movabsq $0x8877665544332211, %rbp
	movabsq $0x1021324354657687, %rbx
	movabsq $0x89abcdef01234567, %r12
	movabsq $0xfedcba9876543210, %r13
	movabsq $0x0f1e2d3c4b5a6978, %r14
	movabsq $0x8070605040302010, %r15
	call accumulate_samples@PLT

	movq (%rsp), %r10
	movq %rax, (%r10)
	xorl %eax, %eax
	movabsq $0x8877665544332211, %r10
	cmpq %r10, %rbp
	je .Lrbp_ok
	orl $1, %eax
.Lrbp_ok:
	movabsq $0x1021324354657687, %r10
	cmpq %r10, %rbx
	je .Lrbx_ok
	orl $2, %eax
.Lrbx_ok:
	movabsq $0x89abcdef01234567, %r10
	cmpq %r10, %r12
	je .Lr12_ok
	orl $4, %eax
.Lr12_ok:
	movabsq $0xfedcba9876543210, %r10
	cmpq %r10, %r13
	je .Lr13_ok
	orl $8, %eax
.Lr13_ok:
	movabsq $0x0f1e2d3c4b5a6978, %r10
	cmpq %r10, %r14
	je .Lr14_ok
	orl $16, %eax
.Lr14_ok:
	movabsq $0x8070605040302010, %r10
	cmpq %r10, %r15
	je .Lr15_ok
	orl $32, %eax
.Lr15_ok:
	addq $8, %rsp
	.cfi_def_cfa_offset 56
	popq %r15
	.cfi_def_cfa_offset 48
	.cfi_restore %r15
	popq %r14
	.cfi_def_cfa_offset 40
	.cfi_restore %r14
	popq %r13
	.cfi_def_cfa_offset 32
	.cfi_restore %r13
	popq %r12
	.cfi_def_cfa_offset 24
	.cfi_restore %r12
	popq %rbx
	.cfi_def_cfa_offset 16
	.cfi_restore %rbx
	popq %rbp
	.cfi_def_cfa_offset 8
	.cfi_restore %rbp
	ret
	.cfi_endproc
	.size call_with_nonvolatile_sentinels, .-call_with_nonvolatile_sentinels

	.section .note.GNU-stack,"",@progbits
