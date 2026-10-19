/**
 * SenderBox — user-input area.
 *
 * Shows the normal chat input by default. When the backend pauses for a
 * sensitive tool-call approval (e.g. read_file) or SQL execution approval,
 * it replaces the input with an inline approval card so the user can allow
 * or deny the action.
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
    const approvalType = approvalPayload?.type as string | undefined;

    if (approvalType === "sql_approval") {
      const sql = approvalPayload?.sql as string | undefined;
      return (
        <div className="sender-row">
          <Alert
            type="warning"
            showIcon
            message="SQL 执行需要您的确认"
            description={
              <Space direction="vertical" size={4} style={{ width: "100%" }}>
                <span>Agent 请求执行以下 SQL 以获取结果，请确认是否允许？</span>
                {sql && (
                  <pre
                    style={{
                      maxHeight: 200,
                      overflow: "auto",
                      background: "#f6f8fa",
                      padding: 12,
                      borderRadius: 6,
                    }}
                  >
                    {sql}
                  </pre>
                )}
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
