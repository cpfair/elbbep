ENTRY(main)

MEMORY
{
  PATCH (rwx) : ORIGIN = @TARGET_END@, LENGTH = 0x6000
}

SECTIONS
{

    .text :
    {
        *(.text)
        *(.text.*)
        *(.rodata)
        *(.rodata*)
    } > PATCH

    DISCARD :
    {
        libc.a ( * )
        libm.a ( * )
        libgcc.a ( * )
        *(.eh_frame)
    }
}
