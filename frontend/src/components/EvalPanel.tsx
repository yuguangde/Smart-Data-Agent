/**
 * EvalPanel — side drawer wrapper around EvalManager.
 */
import { Drawer } from "antd";

import { EvalManager } from "@/components/EvalManager";
import type { EvalManagerProps } from "@/components/EvalManager";

export interface EvalPanelProps extends EvalManagerProps {
  visible: boolean;
  onClose: () => void;
}

export function EvalPanel(props: EvalPanelProps) {
  const { visible, onClose, ...managerProps } = props;
  return (
    <Drawer
      title="评测管理"
      placement="right"
      width={960}
      onClose={onClose}
      open={visible}
    >
      <EvalManager {...managerProps} />
    </Drawer>
  );
}
