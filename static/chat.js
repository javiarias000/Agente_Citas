/**
 * Arcadium Chat Interface
 * WebSocket client for real-time chat with DeyyAgent
 */

class ArcadiumChat {
  constructor() {
    this.ws = null;
    this.sessionId = this.generateSessionId();
    this.messageInput = document.getElementById("messageInput");
    this.sendButton = document.getElementById("sendButton");
    this.messagesContainer = document.getElementById("messagesContainer");
    this.statusIndicator = document.getElementById("statusIndicator");
    this.connectionStatus = document.getElementById("connectionStatus");
    this.typingIndicator = null;

    this.init();
  }

  generateSessionId() {
    // Generate a random session ID
    return "session_" + Math.random().toString(36).substr(2, 9);
  }

  init() {
    // Event listeners
    this.sendButton.addEventListener("click", () => this.sendMessage());
    this.messageInput.addEventListener("keypress", (e) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        this.sendMessage();
      }
    });

    // Auto-focus input
    this.messageInput.focus();

    // Connect WebSocket
    this.connect();

    // Load history on start
    this.loadHistory();
  }

  async connect() {
    const wsProtocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const wsUrl = `${wsProtocol}//${window.location.host}/ws/${this.sessionId}`;

    try {
      this.ws = new WebSocket(wsUrl);
      this.setConnectionStatus("connecting");

      this.ws.onopen = () => {
        console.log("WebSocket connected");
        this.setConnectionStatus("connected");
        this.addSystemMessage("Conectado al asistente");
      };

      this.ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          this.handleMessage(data);
        } catch (error) {
          console.error("Error parsing WebSocket message:", error);
        }
      };

      this.ws.onclose = () => {
        console.log("WebSocket disconnected");
        this.setConnectionStatus("disconnected");
        this.addSystemMessage("Desconectado. Recargando...");

        // Attempt to reconnect after 3 seconds
        setTimeout(() => this.connect(), 3000);
      };

      this.ws.onerror = (error) => {
        console.error("WebSocket error:", error);
        this.setConnectionStatus("disconnected");
      };
    } catch (error) {
      console.error("Failed to connect:", error);
      this.setConnectionStatus("disconnected");
    }
  }

  handleMessage(data) {
    switch (data.type) {
      case "response":
        this.hideTypingIndicator();
        this.addMessage(
          data.content,
          "assistant",
          null, // No tool_calls para frontend
          data.execution_time,
        );
        break;
      case "error":
        this.hideTypingIndicator();
        this.addErrorMessage(data.message);
        break;
      default:
        console.log("Unknown message type:", data.type);
    }
  }

  async sendMessage() {
    const message = this.messageInput.value.trim();
    if (!message || !this.ws || this.ws.readyState !== WebSocket.OPEN) {
      return;
    }

    // Add user message to UI immediately
    this.addMessage(message, "user");
    this.messageInput.value = "";
    this.messageInput.focus();

    // Show typing indicator
    this.showTypingIndicator();

    // Send via WebSocket
    this.ws.send(
      JSON.stringify({
        message: message,
      }),
    );
  }

  addMessage(content, type, executionTime = null) {
    const messageDiv = document.createElement("div");
    messageDiv.className = `message ${type}`;

    const avatar = document.createElement("div");
    avatar.className = `avatar ${type}`;
    avatar.textContent = type === "user" ? "T" : "AI";

    const contentDiv = document.createElement("div");
    contentDiv.className = "message-content";

    // Message text
    const text = document.createElement("div");
    text.textContent = content;
    contentDiv.appendChild(text);

    // Timestamp
    const time = document.createElement("div");
    time.className = "message-time";
    const now = new Date();
    time.textContent = now.toLocaleTimeString([], {
      hour: "2-digit",
      minute: "2-digit",
    });
    contentDiv.appendChild(time);

    // Execution time (for assistant)
    if (executionTime && type === "assistant") {
      const execTime = document.createElement("div");
      execTime.className = "message-time";
      execTime.textContent = `⏱ ${executionTime.toFixed(2)}s`;
      contentDiv.appendChild(execTime);
    }

    messageDiv.appendChild(avatar);
    messageDiv.appendChild(contentDiv);

    this.messagesContainer.appendChild(messageDiv);
    this.scrollToBottom();
  }

  addSystemMessage(content) {
    const div = document.createElement("div");
    div.style.textAlign = "center";
    div.style.color = "#6b7280";
    div.style.fontSize = "0.875rem";
    div.style.margin = "10px 0";
    div.textContent = content;
    this.messagesContainer.appendChild(div);
    this.scrollToBottom();
  }

  addErrorMessage(content) {
    const errorDiv = document.createElement("div");
    errorDiv.className = "error-message";
    errorDiv.textContent = content;
    this.messagesContainer.appendChild(errorDiv);
    this.scrollToBottom();
  }

  showTypingIndicator() {
    if (this.typingIndicator) return;

    this.typingIndicator = document.createElement("div");
    this.typingIndicator.className = "typing-indicator";
    this.typingIndicator.innerHTML = "<span></span><span></span><span></span>";
    this.messagesContainer.appendChild(this.typingIndicator);
    this.scrollToBottom();
  }

  hideTypingIndicator() {
    if (this.typingIndicator) {
      this.typingIndicator.remove();
      this.typingIndicator = null;
    }
  }

  setConnectionStatus(status) {
    this.connectionStatus.className = `connection-status ${status}`;
    this.connectionStatus.textContent =
      status === "connected"
        ? "Conectado"
        : status === "connecting"
          ? "Conectando..."
          : "Desconectado";

    this.statusIndicator.className = `status-indicator ${status}`;
  }

  scrollToBottom() {
    this.messagesContainer.scrollTop = this.messagesContainer.scrollHeight;
  }

  async loadHistory() {
    try {
      const response = await fetch(`/api/history/${this.sessionId}`);
      if (response.ok) {
        const data = await response.json();
        data.messages.forEach((msg) => {
          // Map LangChain message types to our UI types
          const type = msg.type?.includes("Human") ? "user" : "assistant";
          this.addMessage(msg.content, type);
        });
      }
    } catch (error) {
      console.log("No previous history found");
    }
  }
}

// Initialize chat when DOM is ready
document.addEventListener("DOMContentLoaded", () => {
  window.chat = new ArcadiumChat();
});
