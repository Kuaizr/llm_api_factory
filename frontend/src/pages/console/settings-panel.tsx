import { AlertTriangle, Bell, Lock, LogOut, Save, Shield, UserCircle } from "lucide-react";
import { useEffect, useState } from "react";

import { type TelegramConfig } from "./shared";

type ActionResult = { ok: boolean; message: string };

type SettingsViewProps = {
  token: string | null;
  isAdmin: boolean;
  avatarUrl: string;
  telegramConfig: TelegramConfig | null;
  onLogin: (password: string) => void;
  onLogout: () => void;
  onUpdateAvatar: (avatarUrl: string) => void;
  onUpdatePassword: (
    currentPassword: string,
    newPassword: string
  ) => Promise<ActionResult>;
  onUpdateTelegramConfig: (botToken: string, chatId: string) => Promise<ActionResult>;
  onSendTelegramTest: () => Promise<ActionResult>;
};

export const SettingsView = ({
  token,
  isAdmin,
  avatarUrl,
  telegramConfig,
  onLogin,
  onLogout,
  onUpdateAvatar,
  onUpdatePassword,
  onUpdateTelegramConfig,
  onSendTelegramTest,
}: SettingsViewProps) => {
  const [password, setPassword] = useState("");
  const [avatarDraft, setAvatarDraft] = useState(avatarUrl);
  const [avatarMessage, setAvatarMessage] = useState<string | null>(null);

  const [currentAdminPassword, setCurrentAdminPassword] = useState("");
  const [newAdminPassword, setNewAdminPassword] = useState("");
  const [passwordUpdating, setPasswordUpdating] = useState(false);
  const [passwordUpdateError, setPasswordUpdateError] = useState<string | null>(null);
  const [passwordUpdateMessage, setPasswordUpdateMessage] = useState<string | null>(null);

  const [botToken, setBotToken] = useState("");
  const [chatId, setChatId] = useState("");
  const [telegramSaving, setTelegramSaving] = useState(false);
  const [telegramTesting, setTelegramTesting] = useState(false);
  const [telegramMessage, setTelegramMessage] = useState<string | null>(null);
  const [telegramError, setTelegramError] = useState<string | null>(null);

  useEffect(() => {
    setAvatarDraft(avatarUrl);
  }, [avatarUrl]);

  useEffect(() => {
    setChatId(telegramConfig?.chat_id || "");
  }, [telegramConfig?.chat_id]);

  const handlePasswordSubmit = async () => {
    if (!isAdmin || passwordUpdating) return;
    setPasswordUpdateError(null);
    setPasswordUpdateMessage(null);
    setPasswordUpdating(true);
    const result = await onUpdatePassword(currentAdminPassword, newAdminPassword);
    if (result.ok) {
      setPasswordUpdateMessage(result.message);
      setCurrentAdminPassword("");
      setNewAdminPassword("");
    } else {
      setPasswordUpdateError(result.message);
    }
    setPasswordUpdating(false);
  };

  const handleAvatarSave = () => {
    onUpdateAvatar(avatarDraft);
    setAvatarMessage("头像已更新");
    window.setTimeout(() => setAvatarMessage(null), 2000);
  };

  const handleTelegramSave = async () => {
    if (!isAdmin || telegramSaving) return;
    setTelegramError(null);
    setTelegramMessage(null);
    setTelegramSaving(true);
    const result = await onUpdateTelegramConfig(botToken, chatId);
    if (result.ok) {
      setTelegramMessage(result.message);
      setBotToken("");
    } else {
      setTelegramError(result.message);
    }
    setTelegramSaving(false);
  };

  const handleTelegramTest = async () => {
    if (!isAdmin || telegramTesting) return;
    setTelegramError(null);
    setTelegramMessage(null);
    setTelegramTesting(true);
    const result = await onSendTelegramTest();
    if (result.ok) {
      setTelegramMessage(result.message);
    } else {
      setTelegramError(result.message);
    }
    setTelegramTesting(false);
  };

  const loginCard = (
    <div className="bg-[#0f1117] border border-gray-800 rounded-xl p-6">
      <h3 className="text-lg font-bold text-white mb-6 flex items-center gap-2">
        <Shield size={20} className="text-green-500" />
        管理员登录
      </h3>
      <div className="space-y-4 max-w-md">
        <input
          type="password"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          placeholder="输入管理员密码"
          className="w-full bg-gray-900 border border-gray-700 rounded p-2.5 text-sm text-white focus:border-blue-500 focus:outline-none"
        />
        <button
          onClick={() => onLogin(password)}
          className="bg-blue-600 hover:bg-blue-500 text-white px-6 py-2 rounded text-sm font-medium"
        >
          登录
        </button>
      </div>
    </div>
  );

  return (
    <div className="max-w-4xl space-y-8">
      {!isAdmin && loginCard}

      <div className="bg-[#0f1117] border border-gray-800 rounded-xl p-6">
        <h3 className="text-lg font-bold text-white mb-6 flex items-center gap-2">
          <UserCircle size={20} className="text-blue-500" />
          个人资料
        </h3>
        <div className="grid grid-cols-[96px_1fr] gap-4 items-center max-w-2xl">
          <div className="w-20 h-20 rounded-full border border-gray-700 bg-gray-900 overflow-hidden flex items-center justify-center">
            {avatarDraft ? (
              <img
                src={avatarDraft}
                alt="avatar"
                className="w-full h-full object-cover"
                onError={() => setAvatarDraft("")}
              />
            ) : (
              <span className="text-2xl font-semibold text-gray-400">A</span>
            )}
          </div>
          <div className="space-y-3">
            <input
              type="text"
              value={avatarDraft}
              onChange={(event) => setAvatarDraft(event.target.value)}
              placeholder="输入头像图片 URL（留空则使用默认）"
              className="w-full bg-gray-900 border border-gray-700 rounded p-2.5 text-sm text-white focus:border-blue-500 focus:outline-none"
            />
            <div className="flex items-center gap-3">
              <button
                onClick={handleAvatarSave}
                className="bg-gray-800 hover:bg-gray-700 text-white px-4 py-2 rounded text-sm font-medium border border-gray-700 transition"
              >
                更新头像
              </button>
              {isAdmin && (
                <button
                  onClick={onLogout}
                  className="text-red-400 hover:text-red-300 px-2 py-2 text-sm inline-flex items-center gap-1"
                >
                  <LogOut size={14} /> 退出登录
                </button>
              )}
            </div>
            {avatarMessage && <p className="text-xs text-green-400">{avatarMessage}</p>}
          </div>
        </div>
      </div>

      {isAdmin && (
        <>
          <div className="bg-[#0f1117] border border-gray-800 rounded-xl p-6">
            <h3 className="text-lg font-bold text-white mb-6 flex items-center gap-2">
              <Bell size={20} className="text-blue-500" />
              Telegram Bot
            </h3>
            <div className="space-y-4 max-w-2xl">
              <div>
                <label className="block text-xs font-medium text-gray-500 mb-1.5 uppercase">
                  Bot Token
                </label>
                <div className="relative">
                  <input
                    type="text"
                    value={botToken}
                    onChange={(event) => setBotToken(event.target.value)}
                    placeholder={telegramConfig?.bot_token_masked || "输入 Bot Token"}
                    className="w-full bg-gray-900 border border-gray-700 rounded p-2.5 text-sm text-gray-300 font-mono focus:border-blue-500 focus:outline-none transition pl-10"
                  />
                  <Lock size={14} className="absolute left-3.5 top-3 text-gray-600" />
                </div>
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-500 mb-1.5 uppercase">
                  Chat ID / Channel ID
                </label>
                <input
                  type="text"
                  value={chatId}
                  onChange={(event) => setChatId(event.target.value)}
                  className="w-full bg-gray-900 border border-gray-700 rounded p-2.5 text-sm text-gray-300 font-mono focus:border-blue-500 focus:outline-none transition"
                  placeholder="例如: -100123456789"
                />
              </div>

              <div className="pt-2 flex items-center gap-3">
                <button
                  onClick={() => {
                    void handleTelegramSave();
                  }}
                  disabled={telegramSaving}
                  className="bg-blue-600 hover:bg-blue-500 text-white px-6 py-2 rounded text-sm font-medium transition flex items-center gap-2 disabled:opacity-50"
                >
                  <Save size={16} /> {telegramSaving ? "保存中..." : "保存配置"}
                </button>
                <button
                  onClick={() => {
                    void handleTelegramTest();
                  }}
                  disabled={telegramTesting}
                  className="text-gray-400 hover:text-white px-4 py-2 text-sm transition disabled:opacity-50"
                >
                  {telegramTesting ? "发送中..." : "发送测试消息"}
                </button>
              </div>
              {telegramMessage && <p className="text-xs text-green-400">{telegramMessage}</p>}
              {telegramError && <p className="text-xs text-red-400">{telegramError}</p>}
              <p className="text-xs text-gray-600">
                当前状态：{telegramConfig?.configured ? "已配置" : "未配置"}
                {token ? "（已登录）" : "（未登录）"}
              </p>
            </div>
          </div>

          <div className="bg-[#0f1117] border border-gray-800 rounded-xl p-6">
            <h3 className="text-lg font-bold text-white mb-6 flex items-center gap-2">
              <Shield size={20} className="text-green-500" />
              系统安全
            </h3>
            <div className="grid grid-cols-2 gap-8">
              <div className="space-y-4">
                <div>
                  <label className="block text-xs font-medium text-gray-500 mb-1.5 uppercase">
                    当前管理员密码
                  </label>
                  <input
                    type="password"
                    placeholder="输入当前密码"
                    value={currentAdminPassword}
                    onChange={(event) => setCurrentAdminPassword(event.target.value)}
                    className="w-full bg-gray-900 border border-gray-700 rounded p-2.5 text-sm text-white focus:border-blue-500 focus:outline-none transition"
                    disabled={passwordUpdating}
                  />
                </div>
                <div>
                  <label className="block text-xs font-medium text-gray-500 mb-1.5 uppercase">
                    新密码
                  </label>
                  <input
                    type="password"
                    placeholder="至少 4 位"
                    value={newAdminPassword}
                    onChange={(event) => setNewAdminPassword(event.target.value)}
                    className="w-full bg-gray-900 border border-gray-700 rounded p-2.5 text-sm text-white focus:border-blue-500 focus:outline-none transition"
                    disabled={passwordUpdating}
                  />
                </div>
                {passwordUpdateMessage && (
                  <p className="text-xs text-green-400">{passwordUpdateMessage}</p>
                )}
                {passwordUpdateError && <p className="text-xs text-red-400">{passwordUpdateError}</p>}
                <button
                  onClick={() => {
                    void handlePasswordSubmit();
                  }}
                  className="bg-gray-800 hover:bg-gray-700 text-white px-4 py-2 rounded text-sm font-medium border border-gray-700 transition disabled:opacity-50"
                  disabled={passwordUpdating}
                >
                  {passwordUpdating ? "更新中..." : "更新密码"}
                </button>
              </div>
              <div className="bg-yellow-900/10 border border-yellow-900/20 p-4 rounded-lg">
                <div className="flex items-start gap-3">
                  <AlertTriangle className="text-yellow-500 shrink-0" size={18} />
                  <div>
                    <h4 className="text-sm font-bold text-yellow-500 mb-1">安全警告</h4>
                    <p className="text-xs text-yellow-200/60 leading-relaxed">
                      该系统目前仅支持单用户（Admin）模式。请确保您的密码足够复杂。
                    </p>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </>
      )}
    </div>
  );
};
