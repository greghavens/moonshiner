	.text
	.globl accumulate_samples
	.type accumulate_samples, @function
accumulate_samples:
	.cfi_startproc
	pushq %rbp
	.cfi_def_cfa_offset 16
	.cfi_offset %rbp, -16
	movq %rsp, %rbp
	.cfi_def_cfa_register %rbp

	pushq %rbx
	.cfi_offset %rbx, -24
	pushq %r12
	.cfi_offset %r12, -32
	pushq %r13
	.cfi_offset %r13, -40
	pushq %r14
	.cfi_offset %r14, -48
	pushq %r15
	.cfi_offset %r15, -56

	movq %rdi, %r12
	movq %rsi, %r13
	movq %rdx, %r14
	xorl %r15d, %r15d
	xorl %ebx, %ebx

.Lnext_sample:
	cmpq %r13, %r15
	jae .Lsamples_done
	movq (%r12,%r15,8), %rdi
	movq %r15, %rsi
	movq %r14, %rdx
	call abi_transform@PLT
	addq %rax, %rbx
	incq %r15
	jmp .Lnext_sample

.Lsamples_done:
	movq %rbx, %rax
	popq %r15
	.cfi_restore %r15
	popq %r14
	.cfi_restore %r14
	popq %r13
	.cfi_restore %r13
	popq %r12
	.cfi_restore %r12
	popq %rbx
	.cfi_restore %rbx
	leave
	.cfi_def_cfa %rsp, 8
	.cfi_restore %rbp
	ret
	.cfi_endproc
	.size accumulate_samples, .-accumulate_samples

	.section .note.GNU-stack,"",@progbits
