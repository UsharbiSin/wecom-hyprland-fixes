.code32
.global callback_entry
.section .text
callback_entry:
    # OleGetClipboard 成功后进入，复用原函数栈帧。
    # 局部变量：editor、IDataObject、check_only 与 edi 中的格式。
    movl -0x20(%ebp), %eax
    movl 0x4b4(%eax), %eax
    testl %eax, %eax
    jz resume
    # 保留栈帧，准备六个 stdcall 参数和本地 WORD 格式。
    subl $0x20, %esp
    movl %edi, 0x18(%esp)
    movl %eax, (%esp)
    movl -0x1c(%ebp), %edx
    movl %edx, 4(%esp)
    leal 0x18(%esp), %edx
    movl %edx, 8(%esp)
    movl $0, 0xc(%esp)       # RECO_PASTE
    xorl %edx, %edx
    cmpl $0, 8(%ebp)
    sete %dl
    movl %edx, 0x10(%esp)   # fReally = !check_only
    movl $0, 0x14(%esp)     # 普通粘贴；原 Wine 不处理图标显示方式。
    movl (%eax), %ecx
    call *0x20(%ecx)        # IRichEditOleCallback::QueryAcceptData
    subl $0x18, %esp        # stdcall 弹出六个参数，恢复临时栈帧。
    movzwl 0x18(%esp), %edi
    addl $0x20, %esp
    testl %eax, %eax
    jnz callback_handled
resume:
    xorl %edx, %edx         # 复原原来的布尔返回值累加器。
    movl $13, %eax          # 重放被跳转覆盖的指令。
    jmp 0x7ac32cba
callback_handled:
    xorl %edx, %edx
    testl %eax, %eax
    setns %dl              # 其他成功码：应用已完成检查或导入。
    jmp 0x7ac32d0d          # 复用原有对象释放和公共返回路径。
