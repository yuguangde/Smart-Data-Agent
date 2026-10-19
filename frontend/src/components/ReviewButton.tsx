/**
 * ReviewButton — triggers the review agent for the current thread.
 */
import { SafetyCertificateOutlined } from "@ant-design/icons";
import { Button, Tooltip } from "antd";

export interface ReviewButtonProps {
  threadId: string;
  disabled?: boolean;
  onReview: (threadId: string) => void;
}

export function ReviewButton({ threadId, disabled, onReview }: ReviewButtonProps) {
  return (
    <Tooltip title="用另一个模型复核这份报告">
      <Button
        type="text"
        size="small"
        icon={<SafetyCertificateOutlined />}
        disabled={disabled}
        onClick={() => onReview(threadId)}
      >
        复核
      </Button>
    </Tooltip>
  );
}
