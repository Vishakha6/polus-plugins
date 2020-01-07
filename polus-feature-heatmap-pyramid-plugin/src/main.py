from bfio import BioWriter
import bioformats
import javabridge as jutil
import argparse, logging, time, imagesize, os, re, statistics
from filepattern import FilePattern
import numpy as np
from pathlib import Path

STITCH_VARS = ['file','correlation','posX','posY','gridX','gridY'] # image stitching values
STITCH_LINE = "file: {}; corr: {}; position: ({}, {}); grid: ({}, {});\n"

# Initialize the logger
logging.basicConfig(format='%(asctime)s - %(name)-8s - %(levelname)-8s - %(message)s',
                    datefmt='%d-%b-%y %H:%M:%S')
logger = logging.getLogger("main")
logger.setLevel(logging.INFO)

def get_number(s):
    """ Check that s is number
    
    In this plugin, heatmaps are created only for columns that contain numbers. This
    function checks to make sure an input value is able to be converted into a number.

    Inputs:
        s - An input string or number
    Outputs:
        value - Either float(s) or False if s cannot be cast to float
    """
    try:
        return float(s)
    except ValueError:
        return False

def _get_file_dict(fp,fname):
    """ Find an image matching fname in the collection
    
    This function searches files in a FilePattern object to find the image dictionary
    that matches the file name, fname.

    Inputs:
        fp - A FilePattern object
        fname - The name of the file to find in fp
    Outputs:
        current_image - The image dictionary matching fname, None if no matches found
    """
    current_image = None
    for f in fp.iterate():
        if Path(f['file']).name == fname:
            current_image = f
            break
    
    return current_image

def _parse_stitch(stitchPath,fp):
    """ Load and parse image stitching vectors
    
    This function adds keys to the FilePattern object (fp) that indicate image positions
    extracted from the stitching vectors found at the stitchPath location.

    As the stitching vector is parsed, images in the stitching vector are analyzed to
    determine the unique sets of image widths and heights. This information is required
    when generating the heatmap images to create overlays that are identical in size to
    the images in the original pyramid.

    Inputs:
        fp - A FilePattern object
        stitchPath - A path to stitching vectors
    Outputs:
        unique_width - List of all unique widths (in pixels) in the image stitching vectors
        unique_height - List of all unique heights (in pixels) in the image stitching vectors
    """
    # Get the stitch files
    txt_files = [f.name for f in Path(stitchPath).iterdir() if f.is_file() and f.suffix=='.txt']
    global_regex = ".*-global-positions-([0-9]+).txt"
    stitch_vectors = [re.match(global_regex,f) for f in txt_files if re.match(global_regex,f) is not None]
    
    line_regex = r"file: (.*); corr: (.*); position: \((.*), (.*)\); grid: \((.*), (.*)\);"

    unique_width = set()
    unique_height = set()

    # Open each stitching vector
    fnum = 0
    fcheck = 0
    for vector in stitch_vectors:
        vind = vector.groups()[0]
        fpath = os.path.join(stitchPath,vector.group(0))
        with open(fpath,'r') as fr:

            # Read each line in the stitching vector
            line_num = 0
            for line in fr:
                # Read and parse values from the current line
                stitch_groups = re.match(line_regex,line)
                stitch_groups = {key:val for key,val in zip(STITCH_VARS,stitch_groups.groups())}

                # Get the image dictionary associated with the current line
                current_image = _get_file_dict(fp,stitch_groups['file'])
                
                # If an image in the vector doesn't match an image in the collection, then skip it
                if current_image == None:
                    continue

                # Set the stitching vector values in the file dictionary
                current_image.update({key:val for key,val in stitch_groups.items() if key != 'file'})
                current_image['vector'] = vind
                current_image['line'] = line_num
                line_num += 1

                # Get the image size
                current_image['width'], current_image['height'] = imagesize.get(current_image['file'])
                unique_height.update([current_image['height']])
                unique_width.update([current_image['width']])

                # Checkpoint
                fnum += 1
                if fnum//1000 > fcheck:
                    fcheck += 1
                    logger.info('Files parsed: {}'.format(fnum))

    return unique_width,unique_height

def _parse_features(featurePath,fp):
    """ Load and parse the feature list
    
    This function adds mean feature values to the FilePattern object (fp) for every image
    in the FilePattern object if the image is listed in the feature csv file.

    For example, if there are 100 object values in an "area" column for one image, then
    an "area" key is created in the image dictionary with the mean value of all 100 values.

    Inputs:
        fp - A FilePattern object
        stitchPath - A path to stitching vectors
    Outputs:
        unique_width - List of all unique widths (in pixels) in the image stitching vectors
        unique_height - List of all unique heights (in pixels) in the image stitching vectors
    """
    # Get the csv files containing features
    csv_files = [f.name for f in Path(featurePath).iterdir() if f.is_file() and f.suffix=='.csv']

    # Unique list of features and values
    feature_list = {}
    
    # Open each csv files
    for feat_file in csv_files:
        fpath = os.path.join(featurePath,feat_file)
        with open(fpath,'r') as fr:
            # Read the first line, which should contain headers
            first_line = fr.readline()
            headers = first_line.rstrip('\n').split(',')
            var_ind = {key:val for key,val in enumerate(headers)} # map headers to line positions

            # Add unique features to the feature_list
            feature_list.update({key:[] for key in headers if key not in feature_list.keys() and key != 'file'})

            # Get the first line of data
            line = fr.readline()

            # Read each line in the stitching vector
            fnum = 0
            fcheck = 0
            while line:
                # Parse the current line as a dictionary
                p_line = {var_ind[ind]:val for ind,val in enumerate(line.rstrip('\n').split(','))}
                for key,val in p_line.items():
                    v = get_number(val)
                    if isinstance(v,float):
                        p_line[key] = [v]

                # Get the image associated with the current line
                current_image = _get_file_dict(fp,p_line['file'])

                if current_image == None:
                    line = fr.readline()
                    continue

                # Loop through rows until the filename changes
                line = fr.readline()
                np_line = {var_ind[ind]:val for ind,val in enumerate(line.rstrip('\n').split(','))}
                while line and p_line['file'] == np_line['file']:
                    # Store the values in a feature list
                    for key,val in p_line.items():
                        if isinstance(val,list):
                            try:
                                p_line[key].append(float(val))
                            except ValueError:
                                continue

                    # Get the next line
                    line = fr.readline()
                    np_line = {var_ind[ind]:val for ind,val in enumerate(line.rstrip('\n').split(','))}

                # Get the mean of the feature list, save in the file dictionary
                for key,val in p_line.items():
                    if isinstance(val,list):
                        current_image[key] = statistics.mean(val)
                        feature_list[key].append(current_image[key])
                
                # Checkpoint
                fnum += 1
                if fnum//1000 > fcheck:
                    fcheck += 1
                    logger.info('Files parsed: {}'.format(fnum))

    return feature_list

if __name__=="__main__":
    # Setup the argument parsing
    logger.info("Parsing arguments...")
    parser = argparse.ArgumentParser(prog='main', description='Build a heatmap pyramid for features values in a csv as an overlay for another pyramid.')
    parser.add_argument('--features', dest='features', type=str,
                        help='CSV collection containing features', required=True)
    parser.add_argument('--inpDir', dest='inpDir', type=str,
                        help='Input image collection used to build a pyramid that this plugin will make an overlay for', required=True)
    parser.add_argument('--vector', dest='vector', type=str,
                        help='Stitching vector used to buld the image pyramid.', required=True)
    parser.add_argument('--outImages', dest='outImages', type=str,
                        help='Heatmap Output Images', required=True)
    parser.add_argument('--outVectors', dest='outVectors', type=str,
                        help='Heatmap Output Vectors', required=True)
    
    # Parse the arguments
    args = parser.parse_args()
    features = args.features
    logger.info('features = {}'.format(features))
    inpDir = args.inpDir
    logger.info('inpDir = {}'.format(inpDir))
    vector = args.vector
    logger.info('vector = {}'.format(vector))
    outImages = args.outImages
    logger.info('outImages = {}'.format(outImages))
    outVectors = args.outVectors
    logger.info('outVectors = {}'.format(outVectors))

    # Set up the fileparser
    fp = FilePattern(inpDir,'.*.tif')

    # Parse the stitching vector
    logger.info('Parsing stitching vectors...')
    widths, heights = _parse_stitch(vector,fp)

    # Parse the features
    logger.info('Parsing features...')
    feature_list = _parse_features(features,fp)

    # Determine the min, max, and unique values for each data set
    logger.info('Setting feature scales...')
    feature_mins = {}
    feature_ranges = {}
    for key,val in feature_list.items():
        feature_mins[key] = min(val)
        feature_ranges[key] = max(val)-feature_mins[key]
    unique_levels = set()
    for fl in fp.iterate():
        for ft in feature_list:
            try:
                fl[ft] = round((fl[ft] - feature_mins[ft])/feature_ranges[ft] * 254 + 1)
                unique_levels.update([fl[ft]])
            except ZeroDivisionError:
                fl[ft] = 0
                unique_levels.update([0])

    # Start the javabridge with proper java logging
    logger.info('Initializing the javabridge...')
    log_config = Path(__file__).parent.joinpath("log4j.properties")
    jutil.start_vm(args=["-Dlog4j.configuration=file:{}".format(str(log_config.absolute()))],class_path=bioformats.JARS)

    # Generate the heatmap images
    logger.info('Generating heatmap images...')
    for w in widths:
        for h in heights:
            for l in unique_levels:
                out_file = Path(outImages).joinpath(str(w) + '_' + str(h) + '_' + str(l) + '.ome.tif')
                if not out_file.exists():
                    image = np.ones((h,w,1,1,1),dtype=np.uint8)*l
                    bw = BioWriter(str(Path(outImages).joinpath(str(w) + '_' + str(h) + '_' + str(l) + '.ome.tif').absolute()),X=w,Y=h,Z=1,C=1,T=1)
                    bw.write_image(image)
                    bw.close_image()

    # Close the javabridge
    logger.info('Closing the javabridge...')
    jutil.kill_vm()

    # Build the output stitching vector
    logger.info('Generating the heatmap...')
    file_name = '{}_{}_{}.ome.tif'
    for num,feat in enumerate(feature_list):
        fpath = str(Path(outVectors).joinpath('img-global-positions-' + str(num+1) + '.txt').absolute())
        with open(fpath,'w') as fw:
            line = 0
            while True:
                for f in fp.iterate():
                    if f['line'] == line:
                        break
                if f['line'] == line:
                    fw.write("file: {}; corr: {}; position: ({}, {}); grid: ({}, {});\n".format(file_name.format(f['width'],f['height'],f[feat]),
                                                                                                f['correlation'],
                                                                                                f['posX'],
                                                                                                f['posY'],
                                                                                                f['gridX'],
                                                                                                f['gridY']))
                    line += 1
                else:
                    break