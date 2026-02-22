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
  String? _errorMessage;
  String? _successMessage;
  List<Map<String, dynamic>> _generatedFiles = [];

  @override
  void initState() {
    super.initState();
    _loadStrategies();
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

  Future<void> _runTest() async {
    if (_selectedInstrument == null) {
      setState(() {
        _errorMessage = 'Please select an instrument';
      });
      return;
    }

    if (_selectedStrategies.isEmpty) {
      setState(() {
        _errorMessage = 'Please select at least one prediction strategy';
      });
      return;
    }

    setState(() {
      _isRunning = true;
      _errorMessage = null;
      _successMessage = null;
      _generatedFiles = [];
    });

    try {
      // Step 1: Run predictions
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

      // Step 2: Run backtest
      final backtestResponse = await http.post(
        Uri.parse('${widget.apiBaseUrl}/predictions/backtest'),
        headers: {'Content-Type': 'application/json'},
        body: jsonEncode({
          'instrument': _selectedInstrument,
        }),
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

      // Step 3: Load file list
      await _loadFiles();

      setState(() {
        _successMessage = 'Test completed successfully! Generated ${generatedFiles.length} file(s)';
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
        
        // Sort files: comparison files first, then others alphabetically
        files.sort((a, b) {
          final aName = a['name'] as String;
          final bName = b['name'] as String;
          
          final aIsComparison = aName.contains('_index_comparison.xlsx');
          final bIsComparison = bName.contains('_index_comparison.xlsx');
          
          if (aIsComparison && !bIsComparison) return -1;
          if (!aIsComparison && bIsComparison) return 1;
          
          // Both are comparison or both are not - sort alphabetically
          return aName.compareTo(bName);
        });
        
        setState(() {
          _generatedFiles = files;
        });
      }
    } catch (e) {
      // Silently fail - files list is not critical
      print('Error loading files: $e');
    }
  }

  void _downloadFile(String url, String filename) {
    try {
      String fullUrl;
      if (url.startsWith('http')) {
        // Already a full URL
        fullUrl = url;
      } else {
        // Handle relative URLs - check if URL already starts with /api
        // and apiBaseUrl already ends with /api to avoid double /api/api/
        String cleanUrl = url.startsWith('/') ? url : '/$url';
        if (cleanUrl.startsWith('/api') && widget.apiBaseUrl.endsWith('/api')) {
          // Remove /api prefix from URL since apiBaseUrl already includes it
          cleanUrl = cleanUrl.substring(4); // Remove '/api'
        }
        fullUrl = '${widget.apiBaseUrl}$cleanUrl';
      }
      
      // For web, open URL in new tab which will trigger download
      // The API endpoint returns the file with Content-Disposition header
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
            // Instrument Selection
            DropdownButtonFormField<String>(
              value: _selectedInstrument,
              decoration: const InputDecoration(
                labelText: 'Choose Instrument',
                border: OutlineInputBorder(),
                prefixIcon: Icon(Icons.trending_up),
              ),
              items: const [
                DropdownMenuItem<String>(
                  value: 'NIFTY',
                  child: Text('NIFTY'),
                ),
                DropdownMenuItem<String>(
                  value: 'BANKNIFTY',
                  child: Text('BANKNIFTY'),
                ),
              ],
              onChanged: (value) {
                setState(() {
                  _selectedInstrument = value;
                  _selectedStrategies = [];
                  _generatedFiles = [];
                });
              },
            ),
            const SizedBox(height: 24),
            
            // Strategy Selection
            Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              crossAxisAlignment: CrossAxisAlignment.center,
              children: [
                const Expanded(
                  child: Text(
                    'Choose Prediction Strategy (Multi-select)',
                    style: TextStyle(fontSize: 16, fontWeight: FontWeight.bold),
                  ),
                ),
                if (!_isLoadingStrategies && _availableStrategies.isNotEmpty)
                  TextButton.icon(
                    onPressed: () {
                      setState(() {
                        if (_selectedStrategies.length == _availableStrategies.length) {
                          // Deselect all
                          _selectedStrategies.clear();
                        } else {
                          // Select all
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
                    style: TextButton.styleFrom(
                      foregroundColor: Colors.blue,
                      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
                    ),
                  ),
              ],
            ),
            const SizedBox(height: 8),
            if (_isLoadingStrategies)
              const Center(child: CircularProgressIndicator())
            else if (_availableStrategies.isEmpty)
              const Text(
                'No strategies available',
                style: TextStyle(color: Colors.grey),
              )
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
            const SizedBox(height: 8),
            if (_selectedStrategies.isNotEmpty)
              Wrap(
                spacing: 8,
                children: _selectedStrategies.map((strategy) {
                  return Chip(
                    label: Text(strategy),
                    onDeleted: () {
                      setState(() {
                        _selectedStrategies.remove(strategy);
                      });
                    },
                  );
                }).toList(),
              ),
            const SizedBox(height: 24),
            
            // Test Button
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
              label: Text(_isRunning ? 'Running...' : 'Test'),
              style: ElevatedButton.styleFrom(
                backgroundColor: Colors.blue,
                foregroundColor: Colors.white,
                padding: const EdgeInsets.symmetric(vertical: 16),
                textStyle: const TextStyle(fontSize: 18),
              ),
            ),
            
            // Messages
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
            
            // Generated Files
            if (_generatedFiles.isNotEmpty) ...[
              const SizedBox(height: 24),
              const Text(
                'Generated Files',
                style: TextStyle(fontSize: 18, fontWeight: FontWeight.bold),
              ),
              const SizedBox(height: 8),
              ..._generatedFiles.map((file) {
                final filename = file['name'] as String;
                final fileType = file['type'] as String;
                final fileUrl = file['url'] as String;
                final fileSize = file['size'] as int? ?? 0;
                
                // Format file size
                String sizeStr = '${(fileSize / 1024).toStringAsFixed(1)} KB';
                if (fileSize > 1024 * 1024) {
                  sizeStr = '${(fileSize / (1024 * 1024)).toStringAsFixed(1)} MB';
                }
                
                return Card(
                  margin: const EdgeInsets.only(bottom: 8),
                  child: ListTile(
                    leading: Icon(
                      fileType == 'excel' || fileType == 'comparison'
                          ? Icons.table_chart
                          : Icons.description,
                      color: Colors.blue,
                    ),
                    title: Text(
                      filename,
                      style: const TextStyle(fontWeight: FontWeight.bold),
                    ),
                    subtitle: Text('$fileType • $sizeStr'),
                    trailing: IconButton(
                      icon: const Icon(Icons.download),
                      onPressed: () => _downloadFile(fileUrl, filename),
                      tooltip: 'Download',
                    ),
                  ),
                );
              }).toList(),
            ],
            ],
          ),
        ),
      ),
    );
  }
}

