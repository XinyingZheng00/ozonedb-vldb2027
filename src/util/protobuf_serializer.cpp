#include "protobuf_serializer.h"
namespace ozonedb {
namespace protobuf

{
unsigned char* serializeMessage(google::protobuf::Message const& message, int& buffer_length) {
  int size = message.ByteSizeLong();
  buffer_length = size + google::protobuf::io::CodedOutputStream::VarintSize32(size);
  auto* buffer = new unsigned char[buffer_length];
  google::protobuf::io::ArrayOutputStream array_output(buffer, buffer_length);
  google::protobuf::io::CodedOutputStream coded_output(&array_output);

  // Length prefix, then the message
  coded_output.WriteVarint32(size);
  message.SerializeWithCachedSizesToArray(reinterpret_cast<google::protobuf::uint8*>(buffer) + coded_output.ByteCount());
  return buffer;
}

// Parse length-prefixed messages from buffer. The caller owns the messages.
Status deserializeMessages(unsigned char* buffer,
                           size_t bufferSize,
                           std::vector<google::protobuf::Message*>& messages,
                           std::function<google::protobuf::Message*()> const& messageFactory) {
  // A null buffer with a positive size: a peer removed the file between
  // the caller's size check and its read.
  if (buffer == nullptr) {
    return bufferSize == 0 ? Status::kSuccess : Status::kFailure;
  }
  google::protobuf::io::ArrayInputStream array_input(buffer, bufferSize);
  google::protobuf::io::CodedInputStream coded_input(&array_input);
  // Deserialize all messages
  while (static_cast<size_t>(coded_input.CurrentPosition()) < bufferSize) {
    uint32_t size = 0;
    // Reject a malformed length prefix.
    if (!coded_input.ReadVarint32(&size)) {
      std::cerr << getpid() << ":Failed to read varint prefix: bufferSize="
                << bufferSize << " pos=" << coded_input.CurrentPosition()
                << std::endl;
      return Status::kFailure;
    }
    // The message must fit in the rest of the buffer; a corrupt prefix
    // must not push the parser past the end.
    size_t remaining =
        bufferSize - static_cast<size_t>(coded_input.CurrentPosition());
    if (size > remaining) {
      std::cerr << getpid()
                << ":Varint prefix exceeds remaining bytes: size=" << size
                << " remaining=" << remaining << " bufferSize=" << bufferSize
                << std::endl;
      return Status::kFailure;
    }

    google::protobuf::io::CodedInputStream::Limit limit = coded_input.PushLimit(size);
    google::protobuf::Message* message = messageFactory();
    if (!message->MergeFromCodedStream(&coded_input) || !coded_input.ConsumedEntireMessage()) {
      std::cerr << getpid() << ":Failed to parse message: " << bufferSize << " " << message->GetDescriptor()->name() << std::endl;
      delete message;
      message = nullptr;
      return Status::kFailure;
    }
    // Check if required fields are missing
    if (!message->IsInitialized()) {
      std::cerr << getpid() << ":Message missing required fields: "
                << message->InitializationErrorString() << std::endl;
      delete message;
      return Status::kFailure;
    }

    coded_input.PopLimit(limit);
    messages.push_back(message);
  }
  return Status::kSuccess;
}
}  // namespace protobuf
}  // namespace ozonedb