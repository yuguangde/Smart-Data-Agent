/**
 * SenderBox — user-input area.
 *
 * Shows the normal chat input by default. When the backend pauses for a
 * sensitive tool-call approval (e.g. read_file), it replaces the input with
 * an inline approval card so the user can allow or deny the call.
 */
import { Sender } from "@ant-design/x";
import { Alert, Button, Space } from "antd";
import { useState } from "react";

interface Props {
  loading: boolean;
  disabled?: boolean;
  pendingApproval?: boolean;
  approvalPayload?: Record<string, unknown> | null;
  onSend: (text: string) => void;
  onApprove: (approved: boolean) => void;
  onStop: () => void;
  placeholder?: string;
}

export function SenderBox({
  loading,
  disabled,
  pendingApproval,
  approvalPayload,
  onSend,
  onApprove,
  onStop,
  placeholder = "和 Smart Data Agent 聊点什么…（Shift + Enter 换行）",
}: Props) {
  const [value, setValue] = useState("");

  const handleSubmit = (next: string) => {
    const text = next.trim();
    if (!text) return;
    onSend(text);
    setValue("");
  };

  if (pendingApproval) {
    const toolCalls = approvalPayload?.tool_calls as
      | Array<{ name?: string; id?: string }>
      | undefined;
    const names = toolCalls?.map((tc) => tc.name || tc.id || "未知工具") ?? [];
    return (
      <div className="sender-row">
        <Alert
          type="warning"
          showIcon
          message="工具调用需要您的确认"
          description={
            <Space direction="vertical" size={4} style={{ width: "100%" }}>
              <span>
                Agent 请求执行以下敏感工具：{names.join("、")}，请确认是否允许？
              </span>
              <Space>
                <Button
                  type="primary"
                  loading={loading}
                  onClick={() => onApprove(true)}
                >
                  允许
                </Button>
                <Button danger onClick={() => onApprove(false)}>
                  拒绝
                </Button>
              </Space>
            </Space>
          }
          style={{ width: "100%" }}
        />
      </div>
    );
  }

  return (
    <div className="sender-row">
      <Sender
        value={value}
        onChange={setValue}
        onSubmit={handleSubmit}
        onCancel={onStop}
        placeholder={placeholder}
        disabled={disabled}
        loading={loading}
        autoSize={{ minRows: 1, maxRows: 6 }}
      />
    </div>
  );
}
