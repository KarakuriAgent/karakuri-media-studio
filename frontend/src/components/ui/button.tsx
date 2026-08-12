import * as React from 'react'
import { Slot } from '@radix-ui/react-slot'
import { cva, type VariantProps } from 'class-variance-authority'

import { cn } from '@/lib/utils'

const buttonVariants = cva(
  "inline-flex select-none items-center justify-center gap-1.5 whitespace-nowrap rounded-md text-sm font-medium transition-[color,background-color,border-color,box-shadow,transform] duration-150 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50 focus-visible:ring-offset-2 focus-visible:ring-offset-background active:scale-[0.98] disabled:pointer-events-none disabled:opacity-40 disabled:active:scale-100 [&_svg]:pointer-events-none [&_svg]:size-4 [&_svg]:shrink-0",
  {
    variants: {
      variant: {
        default: 'bg-primary text-primary-foreground shadow-elevation-1 hover:bg-primary/90',
        destructive:
          'border border-destructive/40 bg-destructive/15 text-red-300 hover:border-destructive/60 hover:bg-destructive/25',
        outline:
          'border border-border bg-card text-foreground/85 hover:bg-secondary hover:text-foreground',
        secondary: 'bg-secondary text-secondary-foreground hover:bg-secondary/80',
        ghost: 'text-muted-foreground hover:bg-secondary hover:text-foreground',
        link: 'text-primary underline-offset-4 hover:underline',
      },
      size: {
        // 高密度なツール UI なので、既存の `!py-1` 相当の受け皿として xs を持つ。
        xs: 'h-6 gap-1 rounded-sm px-2 text-xs [&_svg]:size-3',
        sm: 'h-8 rounded-md px-2.5 text-xs [&_svg]:size-3.5',
        default: 'h-9 px-3 py-2',
        lg: 'h-10 rounded-md px-6',
        icon: 'size-9',
        'icon-xs': 'size-6 rounded-sm [&_svg]:size-3',
        'icon-sm': 'size-8',
      },
    },
    defaultVariants: {
      variant: 'default',
      size: 'default',
    },
  },
)

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  asChild?: boolean
}

const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild = false, ...props }, ref) => {
    const Comp = asChild ? Slot : 'button'
    return (
      <Comp
        className={cn(buttonVariants({ variant, size, className }))}
        ref={ref}
        {...props}
      />
    )
  },
)
Button.displayName = 'Button'

export { Button, buttonVariants }
