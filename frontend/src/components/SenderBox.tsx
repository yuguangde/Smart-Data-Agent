/**
 * SenderBox — user-input area. Wraps the @ant-design/x `Sender` so the
 * parent can keep the streaming / press-to-send concerns in one place.
 */
import { Sender } from "@ant-design/x";
import { Button, Space } from "antd";
import { useState } from "react";

interface Props {
  loading: boolean;
  disabled?: boolean;
  onSend: (text: string) => void;
  onStop: () => void;
  placeholder?: string;
}

export function SenderBox({
  loading,
  disabled,
  onSend,
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

  return (
    <div className="sender-row">
      <Space.Compact style={{ width: "100%" }}>
        <Sender
          value={value}
          onChange={setValue}
          onSubmit={handleSubmit}
          onCancel={onStop}
          placeholder={placeholder}
          disabled={disabled}
          loading={loading}
          autoSize={{ minRows: 1, maxRows: 6 }}
          style={{ flex: 1 }}
        />
        {loading ? (
          <Button danger onClick={onStop} style={{ marginLeft: 8 }}>
            停止
          </Button>
        ) : (
          <Button
            type="primary"
            disabled={disabled}
            onClick={() => handleSubmit(value)}
            style={{ marginLeft: 8 }}
          >
            发送
          </Button>
        )}
      </Space.Compact>
    </div>
  );
}