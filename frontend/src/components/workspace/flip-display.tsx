import { AnimatePresence, motion } from "motion/react";

import { cn } from "@/lib/utils";

export function FlipDisplay({
  uniqueKey,
  children,
  className,
}: {
  uniqueKey: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("relative overflow-hidden", className)}>
      <AnimatePresence initial={false} mode="popLayout">
        <motion.div
          key={uniqueKey}
          initial={{ y: "100%", opacity: 0, filter: "blur(2px)" }}
          animate={{ y: 0, opacity: 1, filter: "blur(0px)" }}
          exit={{ y: "-100%", opacity: 0, filter: "blur(2px)" }}
          transition={{
            duration: 0.35,
            ease: [0.32, 0.72, 0, 1],
          }}
        >
          {children}
        </motion.div>
      </AnimatePresence>
    </div>
  );
}
