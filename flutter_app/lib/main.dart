import 'package:flutter/material.dart';
import 'package:http/http.dart' as http;
import 'dart:convert';
import 'package:fl_chart/fl_chart.dart';
import 'trend_view_screen.dart';
import 'prediction_test_screen.dart';
import 'dart:html' as html;

String getApiBaseUrl() {
  // Check if API_BASE_URL is set via environment variable (for build-time)
  const envUrl = String.fromEnvironment('API_BASE_URL', defaultValue: '');
  if (envUrl.isNotEmpty) {
    return envUrl;
  }
  
  // For web: try to detect from current location
  try {
    final location = html.window.location;
    // If running on same origin, use relative URL
    if (location.hostname == 'localhost' || location.hostname == '127.0.0.1') {
      // Local development - assume API on port 5000
      return 'http://${location.hostname}:5000/api';
    } else {
      // Production - use same origin
      return '/api';
    }
  } catch (e) {
    // Fallback for non-web platforms or if window is not available
    return '/api';
  }
}

void main() {
  runApp(const MyApp());
}

class MyApp extends StatelessWidget {
  const MyApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Options Trading',
      theme: ThemeData(
        primarySwatch: Colors.blue,
        useMaterial3: true,
      ),
      home: const StockSearchScreen(),
    );
  }
}

class StockSearchScreen extends StatefulWidget {
  const StockSearchScreen({super.key});

  @override
  State<StockSearchScreen> createState() => _StockSearchScreenState();
}

class _StockSearchScreenState extends State<StockSearchScreen> {
  final TextEditingController _stockNameController = TextEditingController();
  late final String _apiBaseUrl = getApiBaseUrl();
  List<Map<String, dynamic>> _matches = [];
  bool _isLoading = false;
  bool _isRefreshing = false;
  bool _isViewing = false;
  String? _errorMessage;
  String? _successMessage;
  String? _selectedSegment; // NSE, BSE, or INDICES
  Map<String, dynamic>? _selectedStock;
  List<Map<String, dynamic>> _optionChainData = [];

  Future<void> _searchStocks() async {
    final query = _stockNameController.text.trim();
    if (query.isEmpty) {
      setState(() {
        _errorMessage = 'Please enter a stock name';
      });
      return;
    }

    setState(() {
      _isLoading = true;
      _errorMessage = null;
      _matches = [];
      _successMessage = null;
      _selectedStock = null;
      _optionChainData = [];
    });

    try {
      final requestBody = <String, dynamic>{
        'query': query,
      };
      if (_selectedSegment != null && _selectedSegment!.isNotEmpty) {
        requestBody['segment'] = _selectedSegment;
      }
      
      final response = await http.post(
        Uri.parse('$_apiBaseUrl/stocks/search'),
        headers: {'Content-Type': 'application/json'},
        body: jsonEncode(requestBody),
      );

      if (response.statusCode == 200) {
        final data = jsonDecode(response.body);
        setState(() {
          _matches = List<Map<String, dynamic>>.from(data['matches']);
          _isLoading = false;
        });
      } else {
        final error = jsonDecode(response.body);
        setState(() {
          _errorMessage = error['error'] ?? 'Failed to search stocks';
          _isLoading = false;
        });
      }
    } catch (e) {
      setState(() {
        _errorMessage = 'Error connecting to server: $e';
        _isLoading = false;
      });
    }
  }

  void _selectStock(Map<String, dynamic> stock) {
    setState(() {
      _selectedStock = stock;
      _errorMessage = null;
      _successMessage = null;
      _optionChainData = [];
    });
  }

  Future<void> _refreshData() async {
    if (_selectedStock == null) return;

    setState(() {
      _isRefreshing = true;
      _errorMessage = null;
      _successMessage = null;
    });

    try {
      final tradingsymbol = _selectedStock!['tradingsymbol'] as String;
      final response = await http.post(
        Uri.parse('$_apiBaseUrl/options/process'),
        headers: {'Content-Type': 'application/json'},
        body: jsonEncode({'tradingsymbol': tradingsymbol}),
      );

      if (response.statusCode == 200) {
        final data = jsonDecode(response.body);
        setState(() {
          _successMessage = data['message'] ?? 'Successfully refreshed options data';
          _isRefreshing = false;
        });
      } else {
        final error = jsonDecode(response.body);
        setState(() {
          _errorMessage = error['error'] ?? 'Failed to refresh data';
          _isRefreshing = false;
        });
      }
    } catch (e) {
      setState(() {
        _errorMessage = 'Error connecting to server: $e';
        _isRefreshing = false;
      });
    }
  }

  Future<void> _viewData() async {
    if (_selectedStock == null) return;

    setState(() {
      _isViewing = true;
      _errorMessage = null;
      _successMessage = null;
      _optionChainData = [];
    });

    try {
      final tradingsymbol = _selectedStock!['tradingsymbol'] as String;
      final response = await http.get(
        Uri.parse('$_apiBaseUrl/options/latest?tradingsymbol=$tradingsymbol'),
        headers: {'Content-Type': 'application/json'},
      );

      if (response.statusCode == 200) {
        final data = jsonDecode(response.body);
        setState(() {
          _optionChainData = List<Map<String, dynamic>>.from(data['rows'] ?? []);
          _isViewing = false;
          if (_optionChainData.isEmpty) {
            _errorMessage = 'No option data found. Try refreshing data first.';
          }
        });
      } else {
        final error = jsonDecode(response.body);
        setState(() {
          _errorMessage = error['error'] ?? 'Failed to fetch option data';
          _isViewing = false;
        });
      }
    } catch (e) {
      setState(() {
        _errorMessage = 'Error connecting to server: $e';
        _isViewing = false;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Options Trading'),
        backgroundColor: Theme.of(context).colorScheme.inversePrimary,
        actions: [
          IconButton(
            icon: const Icon(Icons.analytics),
            tooltip: 'Prediction Testing',
            onPressed: () {
              Navigator.push(
                context,
                MaterialPageRoute(
                  builder: (context) => PredictionTestScreen(
                    apiBaseUrl: _apiBaseUrl,
                  ),
                ),
              );
            },
          ),
        ],
      ),
      body: Padding(
        padding: const EdgeInsets.all(16.0),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            TextField(
              controller: _stockNameController,
              decoration: const InputDecoration(
                labelText: 'Enter stock name (e.g., Reliance, TCS, NIFTY)',
                border: OutlineInputBorder(),
              ),
              onSubmitted: (_) => _searchStocks(),
            ),
            const SizedBox(height: 16),
            DropdownButtonFormField<String>(
              value: _selectedSegment,
              decoration: const InputDecoration(
                labelText: 'Instrument Source (Optional)',
                border: OutlineInputBorder(),
              ),
              items: const [
                DropdownMenuItem<String>(
                  value: null,
                  child: Text('All Sources'),
                ),
                DropdownMenuItem<String>(
                  value: 'NSE',
                  child: Text('NSE'),
                ),
                DropdownMenuItem<String>(
                  value: 'BSE',
                  child: Text('BSE'),
                ),
                DropdownMenuItem<String>(
                  value: 'INDICES',
                  child: Text('INDICES'),
                ),
              ],
              onChanged: (value) {
                setState(() {
                  _selectedSegment = value;
                });
              },
            ),
            const SizedBox(height: 16),
            ElevatedButton(
              onPressed: _isLoading ? null : _searchStocks,
              child: _isLoading
                  ? const SizedBox(
                      height: 20,
                      width: 20,
                      child: CircularProgressIndicator(strokeWidth: 2),
                    )
                  : const Text('Search'),
            ),
            if (_errorMessage != null) ...[
              const SizedBox(height: 16),
              Container(
                padding: const EdgeInsets.all(12),
                decoration: BoxDecoration(
                  color: Colors.red.shade100,
                  borderRadius: BorderRadius.circular(8),
                ),
                child: Text(
                  _errorMessage!,
                  style: const TextStyle(color: Colors.red),
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
                child: Text(
                  _successMessage!,
                  style: const TextStyle(color: Colors.green),
                ),
              ),
            ],
            // Selected stock info and action buttons
            if (_selectedStock != null) ...[
              const SizedBox(height: 24),
              Card(
                color: Colors.blue.shade50,
                child: Padding(
                  padding: const EdgeInsets.all(16.0),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        'Selected: ${_selectedStock!['tradingsymbol']}',
                        style: const TextStyle(
                          fontSize: 18,
                          fontWeight: FontWeight.bold,
                        ),
                      ),
                      const SizedBox(height: 4),
                      Text(
                        '${_selectedStock!['name'] ?? ''} (${_selectedStock!['exchange'] ?? ''})',
                        style: TextStyle(
                          fontSize: 14,
                          color: Colors.grey[600],
                        ),
                      ),
                      const SizedBox(height: 16),
                      Row(
                        children: [
                          Expanded(
                            child: ElevatedButton.icon(
                              onPressed: _isRefreshing ? null : _refreshData,
                              icon: _isRefreshing
                                  ? const SizedBox(
                                      width: 16,
                                      height: 16,
                                      child: CircularProgressIndicator(
                                        strokeWidth: 2,
                                        color: Colors.white,
                                      ),
                                    )
                                  : const Icon(Icons.refresh),
                              label: const Text('Refresh Data'),
                              style: ElevatedButton.styleFrom(
                                backgroundColor: Colors.blue,
                                foregroundColor: Colors.white,
                              ),
                            ),
                          ),
                          const SizedBox(width: 12),
                          Expanded(
                            child: ElevatedButton.icon(
                              onPressed: _isViewing ? null : _viewData,
                              icon: _isViewing
                                  ? const SizedBox(
                                      width: 16,
                                      height: 16,
                                      child: CircularProgressIndicator(
                                        strokeWidth: 2,
                                        color: Colors.white,
                                      ),
                                    )
                                  : const Icon(Icons.visibility),
                              label: const Text('View Data'),
                              style: ElevatedButton.styleFrom(
                                backgroundColor: Colors.green,
                                foregroundColor: Colors.white,
                              ),
                            ),
                          ),
                        ],
                      ),
                    ],
                  ),
                ),
              ),
            ],
            // Stock search results
            if (_matches.isNotEmpty && _selectedStock == null) ...[
              const SizedBox(height: 24),
              const Text(
                'Select a stock:',
                style: TextStyle(fontSize: 18, fontWeight: FontWeight.bold),
              ),
              const SizedBox(height: 8),
              Expanded(
                child: ListView.builder(
                  itemCount: _matches.length,
                  itemBuilder: (context, index) {
                    final match = _matches[index];
                    return Card(
                      margin: const EdgeInsets.only(bottom: 8),
                      child: ListTile(
                        title: Text(match['tradingsymbol'] ?? ''),
                        subtitle: Text('${match['name'] ?? ''} (${match['exchange'] ?? ''})'),
                        trailing: const Icon(Icons.arrow_forward),
                        onTap: () => _selectStock(match),
                      ),
                    );
                  },
                ),
              ),
            ],
            // Option chain data display
            if (_optionChainData.isNotEmpty) ...[
              const SizedBox(height: 24),
              Text(
                'Option Chain (${_optionChainData.length} contracts)',
                style: const TextStyle(fontSize: 18, fontWeight: FontWeight.bold),
              ),
              const SizedBox(height: 8),
              Expanded(
                child: ListView.builder(
                  itemCount: _optionChainData.length,
                  itemBuilder: (context, index) {
                    final option = _optionChainData[index];
                    return Card(
                      margin: const EdgeInsets.only(bottom: 8),
                      child: ExpansionTile(
                        title: Text(
                          '${option['tradingsymbol'] ?? ''}',
                          style: const TextStyle(fontWeight: FontWeight.bold),
                        ),
                        subtitle: Text(
                          'Strike: ${option['strike'] ?? 'N/A'} | ${option['instrument_type'] ?? ''} | Expiry: ${option['expiry'] ?? 'N/A'}',
                        ),
                        children: [
                          Padding(
                            padding: const EdgeInsets.all(16.0),
                            child: Column(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              children: [
                                _buildDataRow('Underlying Price', option['underlying_price']),
                                _buildDataRow('Last Price', option['last_price']),
                                _buildDataRow('Bid Price', option['bid_price']),
                                _buildDataRow('Bid Qty', option['bid_qty']),
                                _buildDataRow('Ask Price', option['ask_price']),
                                _buildDataRow('Ask Qty', option['ask_qty']),
                                _buildDataRow('Volume', option['volume']),
                                _buildDataRow('Open Interest', option['open_interest']),
                                const Divider(),
                                _buildDataRow('Implied Volatility', option['implied_volatility']),
                                _buildDataRow('Delta', option['delta']),
                                _buildDataRow('Gamma', option['gamma']),
                                _buildDataRow('Theta', option['theta']),
                                _buildDataRow('Vega', option['vega']),
                                _buildDataRow('Snapshot Time', option['snapshot_time']),
                                const SizedBox(height: 16),
                                ElevatedButton.icon(
                                  onPressed: () {
                                    Navigator.push(
                                      context,
                                      MaterialPageRoute(
                                        builder: (context) => TrendViewScreen(
                                          optionInstrumentId: option['option_instrument_id'],
                                          tradingsymbol: option['tradingsymbol'] ?? '',
                                          apiBaseUrl: _apiBaseUrl,
                                        ),
                                      ),
                                    );
                                  },
                                  icon: const Icon(Icons.timeline),
                                  label: const Text('View Historic Trend'),
                                  style: ElevatedButton.styleFrom(
                                    backgroundColor: Colors.purple,
                                    foregroundColor: Colors.white,
                                  ),
                                ),
                              ],
                            ),
                          ),
                        ],
                      ),
                    );
                  },
                ),
              ),
            ],
          ],
        ),
      ),
    );
  }

  Widget _buildDataRow(String label, dynamic value) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 4.0),
      child: Row(
        mainAxisAlignment: MainAxisAlignment.spaceBetween,
        children: [
          Text(
            label,
            style: TextStyle(
              fontWeight: FontWeight.w500,
              color: Colors.grey[700],
            ),
          ),
          Text(
            value?.toString() ?? 'N/A',
            style: const TextStyle(fontWeight: FontWeight.bold),
          ),
        ],
      ),
    );
  }

  @override
  void dispose() {
    _stockNameController.dispose();
    super.dispose();
  }
}
