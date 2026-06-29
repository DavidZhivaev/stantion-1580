#pragma once
#include <opencv2/opencv.hpp>
#include <iostream>
#include <string>
#include <cstring>
#include <vector>
#include <array>
#include <concepts>
#include <cstddef>
#include <variant>
#include <type_traits>

#if defined(_WIN32) || defined(_WIN64)
	#include "dtwain.h"
#elif defined(__linux__)
	#include <sane/sane.h>
#endif

template<typename T>
concept ScannerImpl = requires(T scanner, const std::string& name) {
	{ scanner.GetAllAvailableScanners() } -> std::same_as<std::vector<std::string>>;
	{ scanner.OpenConnection(name) } -> std::same_as<bool>;
	{ scanner.CloseConnection() } -> std::same_as<void>;
};

template<typename CallbackT>
concept ScannerCallback = std::invocable<CallbackT, const cv::Mat&>;

#if defined(_WIN32) || defined(_WIN64)

class alignas(64) DTwainSourceHandle final {
private:
	DTWAIN_SOURCE m_source{nullptr};
	bool m_systemInitialized{false};

public:
	constexpr DTwainSourceHandle() noexcept = default;

	DTwainSourceHandle(const DTwainSourceHandle&) = delete;
	DTwainSourceHandle& operator=(const DTwainSourceHandle&) = delete;

	constexpr DTwainSourceHandle(DTwainSourceHandle&& other) noexcept
		: m_source{other.m_source}, m_systemInitialized{other.m_systemInitialized} {
		other.m_source = nullptr;
		other.m_systemInitialized = false;
	}

	constexpr DTwainSourceHandle& operator=(DTwainSourceHandle&& other) noexcept {
		if (this != &other) [[likely]] {
			Close();
			m_source = other.m_source;
			m_systemInitialized = other.m_systemInitialized;
			other.m_source = nullptr;
			other.m_systemInitialized = false;
		}
		return *this;
	}

	~DTwainSourceHandle() noexcept {
		Close();
	}

	void Close() noexcept {
		if (m_source) [[likely]] {
			DTWAIN_CloseSource(m_source);
			m_source = nullptr;
		}
		if (m_systemInitialized) [[likely]] {
			DTWAIN_SysDestroy();
			m_systemInitialized = false;
		}
	}

	[[nodiscard]] constexpr DTWAIN_SOURCE Get() const noexcept {
		return m_source;
	}

	[[nodiscard]] constexpr bool IsValid() const noexcept {
		return m_source != nullptr;
	}

	constexpr void Set(DTWAIN_SOURCE source, bool initialized) noexcept {
		Close();
		m_source = source;
		m_systemInitialized = initialized;
	}
};

class alignas(64) WindowsTWAINScanner final {
private:
	DTwainSourceHandle m_source{};

public:
	constexpr WindowsTWAINScanner() noexcept = default;

	WindowsTWAINScanner(const WindowsTWAINScanner&) = delete;
	WindowsTWAINScanner& operator=(const WindowsTWAINScanner&) = delete;

	WindowsTWAINScanner(WindowsTWAINScanner&&) noexcept = default;
	WindowsTWAINScanner& operator=(WindowsTWAINScanner&&) noexcept = default;

	~WindowsTWAINScanner() noexcept {
		CloseConnection();
	}

	[[nodiscard]] std::vector<std::string> GetAllAvailableScanners() {
		std::vector<std::string> scannerList{};

		if (!DTWAIN_SysInitialize()) [[unlikely]] {
			return scannerList;
		}

		DTWAIN_ARRAY sources{DTWAIN_EnumSources()};
		if (sources) [[likely]] {
			LONG count{DTWAIN_ArrayGetCount(sources)};
			scannerList.reserve(static_cast<size_t>(count));

			for (LONG i{0}; i < count; ++i) {
				DTWAIN_SOURCE src{nullptr};
				DTWAIN_ArrayGetAt(sources, i, &src);

				std::array<char, 256> nameBuffer{};
				DTWAIN_GetSourceProductName(src, nameBuffer.data(), static_cast<LONG>(nameBuffer.size()));
				scannerList.emplace_back(nameBuffer.data());
			}
			DTWAIN_ArrayDestroy(sources);
		}

		DTWAIN_SysDestroy();
		return scannerList;
	}

	[[nodiscard]] bool OpenConnection(const std::string& scannerName) {
		if (!DTWAIN_SysInitialize()) [[unlikely]] {
			return false;
		}

		DTWAIN_SOURCE source{nullptr};
		if (scannerName.empty()) [[unlikely]] {
			source = DTWAIN_SelectDefaultSource();
			std::cout << "[TWAIN] установленно автоматическое подключение с дефолтным сканнером\n";
		} else [[likely]] {
			source = DTWAIN_SelectSourceByName(scannerName.c_str());
		}

		if (!source) [[unlikely]] {
			DTWAIN_SysDestroy();
			return false;
		}

		m_source.Set(source, true);

		DTWAIN_SetImageInfo(m_source.Get(), FALSE);
		DTWAIN_SetResolution(m_source.Get(), 300.0);
		DTWAIN_SetPixelType(m_source.Get(), DTWAIN_PT_RGB);
		DTWAIN_EnableDuplex(m_source.Get(), TRUE);

		return true;
	}

	template<ScannerCallback CallbackT>
	void StartCaptureLoop(CallbackT&& onPageScanned) {
		if (!m_source.IsValid()) [[unlikely]] {
			return;
		}

		std::cout << "[TWAIN] инициализация механизма автоматической подачи документов...\n";

		DTWAIN_ARRAY acquiredData{DTWAIN_AcquireNative(m_source.Get(), DTWAIN_ACQUIREALL, FALSE)};
		if (!acquiredData) [[unlikely]] {
			return;
		}

		LONG pageCount{DTWAIN_ArrayGetCount(acquiredData)};
		for (LONG i{0}; i < pageCount; ++i) {
			HANDLE hDib{nullptr};
			DTWAIN_ArrayGetAt(acquiredData, i, &hDib);
			if (!hDib) [[unlikely]] {
				continue;
			}

			unsigned char* pDibData{static_cast<unsigned char*>(GlobalLock(hDib))};
			BITMAPINFOHEADER* pBi{reinterpret_cast<BITMAPINFOHEADER*>(pDibData)};
			unsigned char* pPixels{pDibData + pBi->biSize};

			cv::Mat rawPage{pBi->biHeight, pBi->biWidth, CV_8UC3, pPixels};
			cv::Mat flippedPage{};
			cv::flip(rawPage, flippedPage, 0);

			onPageScanned(flippedPage);

			GlobalUnlock(hDib);
		}
		DTWAIN_ArrayDestroy(acquiredData);
	}

	void CloseConnection() noexcept {
		m_source.Close();
		std::cout << "[TWAIN] Соединение остановленно.\n";
	}
};

#endif

#if defined(__linux__)

class alignas(64) SaneHandle final {
private:
	SANE_Handle m_handle{nullptr};
	bool m_saneInitialized{false};

public:
	constexpr SaneHandle() noexcept = default;

	SaneHandle(const SaneHandle&) = delete;
	SaneHandle& operator=(const SaneHandle&) = delete;

	constexpr SaneHandle(SaneHandle&& other) noexcept
		: m_handle{other.m_handle}, m_saneInitialized{other.m_saneInitialized} {
		other.m_handle = nullptr;
		other.m_saneInitialized = false;
	}

	constexpr SaneHandle& operator=(SaneHandle&& other) noexcept {
		if (this != &other) [[likely]] {
			Close();
			m_handle = other.m_handle;
			m_saneInitialized = other.m_saneInitialized;
			other.m_handle = nullptr;
			other.m_saneInitialized = false;
		}
		return *this;
	}

	~SaneHandle() noexcept {
		Close();
	}

	void Close() noexcept {
		if (m_handle) [[likely]] {
			sane_close(m_handle);
			m_handle = nullptr;
		}
		if (m_saneInitialized) [[likely]] {
			sane_exit();
			m_saneInitialized = false;
		}
	}

	[[nodiscard]] constexpr SANE_Handle Get() const noexcept {
		return m_handle;
	}

	[[nodiscard]] constexpr bool IsValid() const noexcept {
		return m_handle != nullptr;
	}

	constexpr void Set(SANE_Handle handle, bool initialized) noexcept {
		m_handle = handle;
		m_saneInitialized = initialized;
	}
};

class alignas(64) LinuxSaneScanner final {
private:
	SaneHandle m_handle{};

	bool SetSaneOption(const char* optionName, const void* value) {
		if (!m_handle.IsValid()) return false;

		const SANE_Option_Descriptor* desc{nullptr};
		SANE_Int numOptions{0};
		sane_control_option(m_handle.Get(), 0, SANE_ACTION_GET_VALUE, &numOptions, nullptr);

		for (SANE_Int i = 1; i < numOptions; ++i) {
			desc = sane_get_option_descriptor(m_handle.Get(), i);
			if (desc && desc->name && std::strcmp(desc->name, optionName) == 0) {
				SANE_Int info{0};
				if (sane_control_option(m_handle.Get(), i, SANE_ACTION_SET_VALUE,
					const_cast<void*>(value), &info) == SANE_STATUS_GOOD) {
					std::cout << "[SANE] Set " << optionName << " successfully\n";
					return true;
				}
			}
		}
		return false;
	}

	void EnableDuplex() {
		// Try common duplex option names used by different SANE backends
		SANE_Bool duplexOn = SANE_TRUE;
		if (SetSaneOption("duplex", &duplexOn)) return;
		if (SetSaneOption("adf-mode", "Duplex")) return;
		if (SetSaneOption("source", "ADF Duplex")) return;
		if (SetSaneOption("scan-source", "ADF Duplex")) return;
		std::cout << "[SANE] Duplex option not found or not supported\n";
	}

public:
	constexpr LinuxSaneScanner() noexcept = default;

	LinuxSaneScanner(const LinuxSaneScanner&) = delete;
	LinuxSaneScanner& operator=(const LinuxSaneScanner&) = delete;

	LinuxSaneScanner(LinuxSaneScanner&&) noexcept = default;
	LinuxSaneScanner& operator=(LinuxSaneScanner&&) noexcept = default;

	~LinuxSaneScanner() noexcept {
		CloseConnection();
	}

	[[nodiscard]] std::vector<std::string> GetAllAvailableScanners() {
		std::vector<std::string> scannerList{};
		SANE_Int version{0};

		if (sane_init(&version, nullptr) != SANE_STATUS_GOOD) [[unlikely]] {
			return scannerList;
		}

		const SANE_Device** device_list{nullptr};
		if (sane_get_devices(&device_list, SANE_FALSE) == SANE_STATUS_GOOD) [[likely]] {
			for (int i{0}; device_list[i] != nullptr; ++i) {
				scannerList.emplace_back(device_list[i]->name);
			}
		}
		sane_exit();
		return scannerList;
	}

	[[nodiscard]] bool OpenConnection(const std::string& scannerName) {
		SANE_Int version{0};
		SANE_Status status{sane_init(&version, nullptr)};

		if (status != SANE_STATUS_GOOD) [[unlikely]] {
			std::cerr << "[SANE] Ошибка инициализации.\n";
			return false;
		}

		SANE_Handle handle{nullptr};
		status = sane_open(scannerName.c_str(), &handle);
		if (status != SANE_STATUS_GOOD) [[unlikely]] {
			std::cerr << "[SANE] Невозможно открыть ресурс: " << scannerName << '\n';
			sane_exit();
			return false;
		}

		m_handle.Set(handle, true);
		EnableDuplex();
		return true;
	}

	template<ScannerCallback CallbackT>
	void StartCaptureLoop(CallbackT&& onPageScanned) {
		if (!m_handle.IsValid()) [[unlikely]] {
			return;
		}

		while (true) {
			SANE_Status status{sane_start(m_handle.Get())};
			if (status != SANE_STATUS_GOOD) [[unlikely]] {
				break;
			}

			SANE_Parameters params{};
			sane_get_parameters(m_handle.Get(), &params);

			// Handle unknown line count (params.lines == -1) for ADF scanners
			std::vector<SANE_Byte> heapBuffer{};
			const bool unknownLines = (params.lines <= 0);
			if (!unknownLines) {
				heapBuffer.resize(static_cast<size_t>(params.bytes_per_line) * static_cast<size_t>(params.lines));
			}

			size_t memoryOffset{0};
			constexpr SANE_Int maxChunkSize{32 * 1024};
			SANE_Int processedBytes{0};

			while (true) {
				if (unknownLines) {
					heapBuffer.resize(memoryOffset + maxChunkSize);
				}
				status = sane_read(m_handle.Get(), heapBuffer.data() + memoryOffset, maxChunkSize, &processedBytes);
				if (status == SANE_STATUS_EOF || processedBytes == 0) [[unlikely]] {
					break;
				}
				memoryOffset += static_cast<size_t>(processedBytes);
			}

			if (memoryOffset == 0 || params.bytes_per_line <= 0) [[unlikely]] {
				continue;
			}

			const int actualLines = unknownLines
				? static_cast<int>(memoryOffset / static_cast<size_t>(params.bytes_per_line))
				: params.lines;

			cv::Mat linuxRgbFrame{actualLines, params.pixels_per_line, CV_8UC3, heapBuffer.data()};
			cv::Mat standardBgrFrame{};
			cv::cvtColor(linuxRgbFrame, standardBgrFrame, cv::COLOR_RGB2BGR);

			onPageScanned(standardBgrFrame);
		}
	}

	void CloseConnection() noexcept {
		m_handle.Close();
		std::cout << "[SANE] Соединение остановленно.\n";
	}
};

#endif

#if defined(_WIN32) || defined(_WIN64)
using ScannerVariant = std::variant<WindowsTWAINScanner>;
#elif defined(__linux__)
using ScannerVariant = std::variant<LinuxSaneScanner>;
#else
static_assert(false, "Ваша операционна система непригодна для этой версии HAL.");
#endif

class alignas(64) HardwareScanner final {
private:
	ScannerVariant m_scanner{};

public:
	enum class ScannerAPI : uint8_t {
#if defined(_WIN32) || defined(_WIN64)
		TWAIN = 0,
#elif defined(__linux__)
		SANE = 0,
#endif
	};

#if defined(_WIN32) || defined(_WIN64)
	explicit constexpr HardwareScanner(ScannerAPI api = ScannerAPI::TWAIN) noexcept {
		switch (api) {
			case ScannerAPI::TWAIN:
				m_scanner = WindowsTWAINScanner{};
				break;
			default:
				m_scanner = WindowsTWAINScanner{};
				break;
		}
	}
#elif defined(__linux__)
	explicit constexpr HardwareScanner(ScannerAPI api = ScannerAPI::SANE) noexcept {
		switch (api) {
			case ScannerAPI::SANE:
				m_scanner = LinuxSaneScanner{};
				break;
			default:
				m_scanner = LinuxSaneScanner{};
				break;
		}
	}
#endif

	HardwareScanner(const HardwareScanner&) = delete;
	HardwareScanner& operator=(const HardwareScanner&) = delete;

	HardwareScanner(HardwareScanner&&) noexcept = default;
	HardwareScanner& operator=(HardwareScanner&&) noexcept = default;

	~HardwareScanner() noexcept = default;

	[[nodiscard]] std::vector<std::string> GetAllAvailableScanners() {
		return std::visit([](auto&& scanner) -> std::vector<std::string> {
			return scanner.GetAllAvailableScanners();
		}, m_scanner);
	}

	[[nodiscard]] bool OpenConnection(const std::string& scannerName) {
		return std::visit([&scannerName](auto&& scanner) -> bool {
			return scanner.OpenConnection(scannerName);
		}, m_scanner);
	}

	template<ScannerCallback CallbackT>
	void StartCaptureLoop(CallbackT&& onPageScanned) {
		std::visit([&onPageScanned](auto&& scanner) {
			scanner.StartCaptureLoop(std::forward<CallbackT>(onPageScanned));
		}, m_scanner);
	}

	void CloseConnection() noexcept {
		std::visit([](auto&& scanner) {
			scanner.CloseConnection();
		}, m_scanner);
	}
};

#if defined(_WIN32) || defined(_WIN64)
static_assert(ScannerImpl<WindowsTWAINScanner>, "WindowsTWAINScanner must satisfy ScannerImpl concept");
#elif defined(__linux__)
static_assert(ScannerImpl<LinuxSaneScanner>, "LinuxSaneScanner must satisfy ScannerImpl concept");
#endif
