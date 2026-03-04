import 'package:flutter/material.dart';
import 'package:http/http.dart' as http;
import 'dart:convert';
import 'dart:html' as html;

class PredictionTestScreen extends StatefulWidget {
  final String apiBaseUrl;

  const PredictionTestScreen({
    super.key,
    required this.apiBaseUrl,
  });

  @override
  State<PredictionTestScreen> createState() => _PredictionTestScreenState();
}

class _PredictionTestScreenState extends State<PredictionTestScreen> {
  String? _selectedInstrument;
  List<String> _availableStrategies = [];
  List<String> _selectedStrategies = [];
  bool _isLoadingStrategies = false;
  bool _isRunning = false;
  bool _isBackfilling = false;
  bool _runE2EBacktest = true;

  String? _errorMessage;
  String? _successMessage;

  List<Map<String, dynamic>> _generatedFiles = [];
  List<Map<String, dynamic>> _indexSummary = [];
  List<Map<String, dynamic>> _e2eSummary = [];

  Map<String, dynamic>? _underlyingRange;
  Map<String, dynamic>? _optionsRange;

  late final TextEditingController _startDateController;
  late final TextEditingController _endDateController;

  @override
  void initState() {
    super.initState();
    final now = DateTime.now();
    final start = now.subtract(const Duration(days: 30));
    _startDateController = TextEditingController(text: _fmtDate(start));
    _endDateController = TextEditingController(text: _fmtDate(now));
    _loadStrategies();
  }

  @override
  void dispose() {
    _startDateController.dispose();
    _endDateController.dispose();
    super.dispose();
  }

  String _fmtDate(DateTime d) {
    final mm = d.month.toString().padLeft(2, '0');
    final dd = d.day.toString().padLeft(2, '0');
    return '${d.year}-$mm-$dd';
  }

  Future<void> _pickDate(TextEditingController controller) async {
    final initial = DateTime.tryParse(controller.text) ?? DateTime.now();
    final picked = await showDatePicker(
      context: context,
      initialDate: initial,
      firstDate: DateTime(2010, 1, 1),
      lastDate: DateTime(2100, 12, 31),
    );
    if (picked != null) {
      setState(() {
        controller.text = _fmtDate(picked);
      });
    }
  }

  Future<void> _loadStrategies() async {
    setState(() {
      _isLoadingStrategies = true;
      _errorMessage = null;
    });

    try {
      final response = await http.get(
        Uri.parse('${widget.apiBaseUrl}/predictions/strategies'),
        headers: {'Content-Type': 'application/json'},
      );

      if (response.statusCode == 200) {
        final data = jsonDecode(response.body);
        setState(() {
          _availableStrategies = List<String>.from(data['strategies'] ?? []);
          _isLoadingStrategies = false;
        });
      } else {
        final error = jsonDecode(response.body);
        setState(() {
          _errorMessage = error['error'] ?? 'Failed to load strategies';
          _isLoadingStrategies = false;
        });
      }
    } catch (e) {
      setState(() {
        _errorMessage = 'Error connecting to server: $e';
        _isLoadingStrategies = false;
      });
    }
  }

  Future<void> _runBackfill() async {
    if (_selectedInstrument == null) {
      setState(() {
        _errorMessage = 'Select instrument before backfill';
      });
      return;
    }

    setState(() {
      _isBackfilling = true;
      _errorMessage = null;
      _successMessage = null;
    });

    try {
      final endpoint = _selectedInstrument == 'NIFTY'
          ? '/backfill/nifty'
          : '/backfill/banknifty';

      final response = await http.post(
        Uri.parse('${widget.apiBaseUrl}$endpoint'),
        headers: {'Content-Type': 'application/json'},
        body: jsonEncode({
          'start_date': _startDateController.text.trim(),
          'end_date': _endDateController.text.trim(),
        }),
      );

      if (response.statusCode != 200) {
        final error = jsonDecode(response.body);
        setState(() {
          _errorMessage = error['error'] ?? 'Backfill failed';
          _isBackfilling = false;
        });
        return;
      }

      await _loadBackfillRanges();
      setState(() {
        _successMessage = 'Backfill completed for $_selectedInstrument';
        _isBackfilling = false;
      });
    } catch (e) {
      setState(() {
        _errorMessage = 'Backfill error: $e';
        _isBackfilling = false;
      });
    }
  }

  Future<void> _loadBackfillRanges() async {
    if (_selectedInstrument == null) {
      return;
    }

    try {
      final underlyingResponse = await http.get(
        Uri.parse('${widget.apiBaseUrl}/backfill/range/underlying?underlying=$_selectedInstrument'),
      );
      final optionsResponse = await http.get(
        Uri.parse('${widget.apiBaseUrl}/backfill/range/options?underlying=$_selectedInstrument'),
      );

      if (underlyingResponse.statusCode == 200 && optionsResponse.statusCode == 200) {
        final under = jsonDecode(underlyingResponse.body);
        final opt = jsonDecode(optionsResponse.body);
        setState(() {
          _underlyingRange = under['result'] as Map<String, dynamic>?;
          _optionsRange = opt['result'] as Map<String, dynamic>?;
        });
      }
    } catch (_) {
      // non-blocking
    }
  }

  Future<void> _runTest() async {
    if (_selectedInstrument == null) {
      setState(() {
        _errorMessage = 'Please select an instrument';
      });
      return;
    }

    setState(() {
      _isRunning = true;
      _errorMessage = null;
      _successMessage = null;
      _generatedFiles = [];
      _indexSummary = [];
      _e2eSummary = [];
    });

    try {
      final predictionResponse = await http.post(
        Uri.parse('${widget.apiBaseUrl}/predictions/run'),
        headers: {'Content-Type': 'application/json'},
        body: jsonEncode({
          'instrument': _selectedInstrument,
          'strategies': _selectedStrategies,
          'use_agentic': true,
        }),
      );

      if (predictionResponse.statusCode != 200) {
        final error = jsonDecode(predictionResponse.body);
        setState(() {
          _errorMessage = error['error'] ?? 'Failed to run predictions';
          _isRunning = false;
        });
        return;
      }

      final predictionData = jsonDecode(predictionResponse.body);
      final generatedFiles = List<String>.from(predictionData['files'] ?? []);

      final backtestResponse = await http.post(
        Uri.parse('${widget.apiBaseUrl}/predictions/backtest'),
        headers: {'Content-Type': 'application/json'},
        body: jsonEncode({'instrument': _selectedInstrument}),
      );

      if (backtestResponse.statusCode != 200) {
        final error = jsonDecode(backtestResponse.body);
        setState(() {
          _errorMessage = 'Predictions generated but backtest failed: ${error['error']}';
          _isRunning = false;
        });
        return;
      }

      final backtestData = jsonDecode(backtestResponse.body);
      if (backtestData['comparison_file'] != null) {
        generatedFiles.add(backtestData['comparison_file']);
      }
      final indexSummary = List<Map<String, dynamic>>.from(backtestData['summary'] ?? []);

      List<Map<String, dynamic>> e2eSummary = [];
      if (_runE2EBacktest) {
        final e2eResponse = await http.post(
          Uri.parse('${widget.apiBaseUrl}/predictions/backtest/e2e'),
          headers: {'Content-Type': 'application/json'},
          body: jsonEncode({'instrument': _selectedInstrument}),
        );
        if (e2eResponse.statusCode == 200) {
          final e2eData = jsonDecode(e2eResponse.body);
          if (e2eData['comparison_file'] != null) {
            generatedFiles.add(e2eData['comparison_file']);
          }
          e2eSummary = List<Map<String, dynamic>>.from(e2eData['summary'] ?? []);
        }
      }

      await _loadFiles();

      setState(() {
        _indexSummary = indexSummary;
        _e2eSummary = e2eSummary;
        _successMessage = 'Run complete for $_selectedInstrument. Predictions + backtests generated.';
        _isRunning = false;
      });
    } catch (e) {
      setState(() {
        _errorMessage = 'Error running test: $e';
        _isRunning = false;
      });
    }
  }

  Future<void> _loadFiles() async {
    try {
      final response = await http.get(
        Uri.parse('${widget.apiBaseUrl}/predictions/files?instrument=${_selectedInstrument ?? ''}'),
        headers: {'Content-Type': 'application/json'},
      );

      if (response.statusCode == 200) {
        final data = jsonDecode(response.body);
        final files = List<Map<String, dynamic>>.from(data['files'] ?? []);

        files.sort((a, b) {
          final aName = a['name'] as String;
          final bName = b['name'] as String;

          final aIsComparison = aName.contains('_comparison.xlsx') || aName.contains('_index_comparison.xlsx');
          final bIsComparison = bName.contains('_comparison.xlsx') || bName.contains('_index_comparison.xlsx');

          if (aIsComparison && !bIsComparison) return -1;
          if (!aIsComparison && bIsComparison) return 1;
          return aName.compareTo(bName);
        });

        setState(() {
          _generatedFiles = files;
        });
      }
    } catch (e) {
      print('Error loading files: $e');
    }
  }

  void _downloadFile(String url, String filename) {
    try {
      String fullUrl;
      if (url.startsWith('http')) {
        fullUrl = url;
      } else {
        String cleanUrl = url.startsWith('/') ? url : '/$url';
        if (cleanUrl.startsWith('/api') && widget.apiBaseUrl.endsWith('/api')) {
          cleanUrl = cleanUrl.substring(4);
        }
        fullUrl = '${widget.apiBaseUrl}$cleanUrl';
      }

      html.window.open(fullUrl, '_blank');
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text('Downloading $filename...'),
            duration: const Duration(seconds: 2),
          ),
        );
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text('Error downloading file: $e'),
            backgroundColor: Colors.red,
          ),
        );
      }
    }
  }

  Widget _buildRangeRow(String label, Map<String, dynamic>? range) {
    if (range == null) {
      return Text('$label: -');
    }
    final minDate = range['min_date']?.toString() ?? '-';
    final maxDate = range['max_date']?.toString() ?? '-';
    final rowCount = range['row_count']?.toString() ?? '0';
    return Text('$label: $minDate to $maxDate ($rowCount rows)');
  }

  Widget _buildIndexSummary() {
    if (_indexSummary.isEmpty) return const SizedBox.shrink();
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        const SizedBox(height: 24),
        const Text('Index Backtest Summary', style: TextStyle(fontSize: 18, fontWeight: FontWeight.bold)),
        const SizedBox(height: 8),
        ..._indexSummary.map((row) {
          final strategy = row['strategy_name']?.toString() ?? '-';
          final acc = (row['index_prediction_accuracy'] ?? 0).toString();
          final rec = (row['index_prediction_recall'] ?? 0).toString();
          return Card(
            margin: const EdgeInsets.only(bottom: 8),
            child: ListTile(
              title: Text(strategy),
              subtitle: Text('Accuracy: $acc | Recall: $rec'),
            ),
          );
        }),
      ],
    );
  }

  Widget _buildE2ESummary() {
    if (_e2eSummary.isEmpty) return const SizedBox.shrink();
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        const SizedBox(height: 24),
        const Text('E2E Summary (Index + Option)', style: TextStyle(fontSize: 18, fontWeight: FontWeight.bold)),
        const SizedBox(height: 8),
        ..._e2eSummary.map((row) {
          final name = row['strategy_combination']?.toString() ?? '-';
          final idxAcc = (row['index_prediction_accuracy'] ?? 0).toString();
          final optAcc = (row['option_selector_accuracy'] ?? 0).toString();
          final net = (row['net_profit'] ?? 0).toString();
          return Card(
            margin: const EdgeInsets.only(bottom: 8),
            child: ListTile(
              title: Text(name),
              subtitle: Text('IndexAcc: $idxAcc | OptionAcc: $optAcc | NetPnL: $net'),
            ),
          );
        }),
      ],
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Prediction Testing'),
        backgroundColor: Theme.of(context).colorScheme.inversePrimary,
      ),
      body: SingleChildScrollView(
        child: Padding(
          padding: const EdgeInsets.all(16.0),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              DropdownButtonFormField<String>(
                value: _selectedInstrument,
                decoration: const InputDecoration(
                  labelText: 'Choose Instrument',
                  border: OutlineInputBorder(),
                  prefixIcon: Icon(Icons.trending_up),
                ),
                items: const [
                  DropdownMenuItem<String>(value: 'NIFTY', child: Text('NIFTY')),
                  DropdownMenuItem<String>(value: 'BANKNIFTY', child: Text('BANKNIFTY')),
                ],
                onChanged: (value) {
                  setState(() {
                    _selectedInstrument = value;
                    _selectedStrategies = [];
                    _generatedFiles = [];
                    _indexSummary = [];
                    _e2eSummary = [];
                    _underlyingRange = null;
                    _optionsRange = null;
                  });
                  _loadBackfillRanges();
                },
              ),
              const SizedBox(height: 16),

              Card(
                child: Padding(
                  padding: const EdgeInsets.all(12),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      const Text('Data Backfill', style: TextStyle(fontSize: 16, fontWeight: FontWeight.bold)),
                      const SizedBox(height: 8),
                      Row(
                        children: [
                          Expanded(
                            child: TextFormField(
                              controller: _startDateController,
                              readOnly: true,
                              decoration: const InputDecoration(labelText: 'Start Date', border: OutlineInputBorder()),
                              onTap: () => _pickDate(_startDateController),
                            ),
                          ),
                          const SizedBox(width: 8),
                          Expanded(
                            child: TextFormField(
                              controller: _endDateController,
                              readOnly: true,
                              decoration: const InputDecoration(labelText: 'End Date', border: OutlineInputBorder()),
                              onTap: () => _pickDate(_endDateController),
                            ),
                          ),
                        ],
                      ),
                      const SizedBox(height: 8),
                      Row(
                        children: [
                          Expanded(
                            child: ElevatedButton.icon(
                              onPressed: _isBackfilling ? null : _runBackfill,
                              icon: _isBackfilling
                                  ? const SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white))
                                  : const Icon(Icons.sync),
                              label: Text(_isBackfilling ? 'Backfilling...' : 'Run Backfill'),
                            ),
                          ),
                          const SizedBox(width: 8),
                          OutlinedButton.icon(
                            onPressed: _loadBackfillRanges,
                            icon: const Icon(Icons.refresh),
                            label: const Text('Refresh Ranges'),
                          ),
                        ],
                      ),
                      const SizedBox(height: 8),
                      _buildRangeRow('Underlying Daily', _underlyingRange?['daily'] as Map<String, dynamic>?),
                      _buildRangeRow('Underlying 5m', _underlyingRange?['candles_5m'] as Map<String, dynamic>?),
                      _buildRangeRow('Options Snapshots', _optionsRange?['snapshots'] as Map<String, dynamic>?),
                    ],
                  ),
                ),
              ),

              const SizedBox(height: 24),
              Row(
                mainAxisAlignment: MainAxisAlignment.spaceBetween,
                crossAxisAlignment: CrossAxisAlignment.center,
                children: [
                  const Expanded(
                    child: Text(
                      'Choose Prediction Strategy (optional, empty = ALL)',
                      style: TextStyle(fontSize: 16, fontWeight: FontWeight.bold),
                    ),
                  ),
                  if (!_isLoadingStrategies && _availableStrategies.isNotEmpty)
                    TextButton.icon(
                      onPressed: () {
                        setState(() {
                          if (_selectedStrategies.length == _availableStrategies.length) {
                            _selectedStrategies.clear();
                          } else {
                            _selectedStrategies = List<String>.from(_availableStrategies);
                          }
                        });
                      },
                      icon: Icon(
                        _selectedStrategies.length == _availableStrategies.length
                            ? Icons.check_box
                            : Icons.check_box_outline_blank,
                      ),
                      label: Text(
                        _selectedStrategies.length == _availableStrategies.length
                            ? 'Deselect All'
                            : 'Select All',
                      ),
                    ),
                ],
              ),
              const SizedBox(height: 8),
              if (_isLoadingStrategies)
                const Center(child: CircularProgressIndicator())
              else if (_availableStrategies.isEmpty)
                const Text('No strategies available', style: TextStyle(color: Colors.grey))
              else
                Container(
                  constraints: const BoxConstraints(maxHeight: 300),
                  decoration: BoxDecoration(
                    border: Border.all(color: Colors.grey),
                    borderRadius: BorderRadius.circular(4),
                  ),
                  child: ListView.builder(
                    shrinkWrap: true,
                    itemCount: _availableStrategies.length,
                    itemBuilder: (context, index) {
                      final strategy = _availableStrategies[index];
                      final isSelected = _selectedStrategies.contains(strategy);

                      return CheckboxListTile(
                        title: Text(strategy),
                        value: isSelected,
                        onChanged: (bool? value) {
                          setState(() {
                            if (value == true) {
                              _selectedStrategies.add(strategy);
                            } else {
                              _selectedStrategies.remove(strategy);
                            }
                          });
                        },
                      );
                    },
                  ),
                ),

              SwitchListTile(
                value: _runE2EBacktest,
                onChanged: (v) => setState(() => _runE2EBacktest = v),
                title: const Text('Run E2E Backtest (Option Selector Metrics)'),
              ),

              const SizedBox(height: 12),
              ElevatedButton.icon(
                onPressed: _isRunning ? null : _runTest,
                icon: _isRunning
                    ? const SizedBox(
                        width: 20,
                        height: 20,
                        child: CircularProgressIndicator(
                          strokeWidth: 2,
                          color: Colors.white,
                        ),
                      )
                    : const Icon(Icons.play_arrow),
                label: Text(_isRunning ? 'Running...' : 'Run Predictions + Backtest'),
                style: ElevatedButton.styleFrom(
                  backgroundColor: Colors.blue,
                  foregroundColor: Colors.white,
                  padding: const EdgeInsets.symmetric(vertical: 16),
                  textStyle: const TextStyle(fontSize: 18),
                ),
              ),

              if (_errorMessage != null) ...[
                const SizedBox(height: 16),
                Container(
                  padding: const EdgeInsets.all(12),
                  decoration: BoxDecoration(
                    color: Colors.red.shade100,
                    borderRadius: BorderRadius.circular(8),
                  ),
                  child: Row(
                    children: [
                      const Icon(Icons.error, color: Colors.red),
                      const SizedBox(width: 8),
                      Expanded(
                        child: Text(
                          _errorMessage!,
                          style: const TextStyle(color: Colors.red),
                        ),
                      ),
                    ],
                  ),
                ),
              ],

              if (_successMessage != null) ...[
                const SizedBox(height: 16),
                Container(
                  padding: const EdgeInsets.all(12),
                  decoration: BoxDecoration(
                    color: Colors.green.shade100,
                    borderRadius: BorderRadius.circular(8),
                  ),
                  child: Row(
                    children: [
                      const Icon(Icons.check_circle, color: Colors.green),
                      const SizedBox(width: 8),
                      Expanded(
                        child: Text(
                          _successMessage!,
                          style: const TextStyle(color: Colors.green),
                        ),
                      ),
                    ],
                  ),
                ),
              ],

              _buildIndexSummary(),
              _buildE2ESummary(),

              if (_generatedFiles.isNotEmpty) ...[
                const SizedBox(height: 24),
                const Text('Generated Files', style: TextStyle(fontSize: 18, fontWeight: FontWeight.bold)),
                const SizedBox(height: 8),
                ..._generatedFiles.map((file) {
                  final filename = file['name'] as String;
                  final fileType = file['type'] as String;
                  final fileUrl = file['url'] as String;
                  final fileSize = file['size'] as int? ?? 0;

                  String sizeStr = '${(fileSize / 1024).toStringAsFixed(1)} KB';
                  if (fileSize > 1024 * 1024) {
                    sizeStr = '${(fileSize / (1024 * 1024)).toStringAsFixed(1)} MB';
                  }

                  return Card(
                    margin: const EdgeInsets.only(bottom: 8),
                    child: ListTile(
                      leading: Icon(
                        fileType == 'excel' || fileType == 'comparison' ? Icons.table_chart : Icons.description,
                        color: Colors.blue,
                      ),
                      title: Text(filename, style: const TextStyle(fontWeight: FontWeight.bold)),
                      subtitle: Text('$fileType | $sizeStr'),
                      trailing: IconButton(
                        icon: const Icon(Icons.download),
                        onPressed: () => _downloadFile(fileUrl, filename),
                        tooltip: 'Download',
                      ),
                    ),
                  );
                }),
              ],
            ],
          ),
        ),
      ),
    );
  }
}
